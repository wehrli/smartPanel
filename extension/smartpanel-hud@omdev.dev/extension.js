// smartPanel HUD — GNOME Shell 50 extension (D3, epic E23).
//
// Draws today's Claude spend, the Claude sessions and four system metrics straight on
// the desktop, behind every window, by reading the smartPanel app's D-Bus API
// (dev.omdev.SmartPanel1, smartpanel/dbus/panel1.xml). The app owns all state and the
// `hud.enabled` / `hud.corner` / `hud.margin` settings; this extension only draws.
//
// - Placement: a child of Main.layoutManager._backgroundGroup, kept on top of the
//   wallpaper actors. That group is a Meta.BackgroundGroup, which mutter's stack sync
//   always keeps below every window actor. (A plain actor in global.window_group would
//   be restacked ABOVE the windows on the next sync, so it must live inside the group.)
//   It is hidden with the windows in the overview and on the lock screen for free.
// - Input: nothing is reactive, so clicks fall through to the desktop.
// - Version: on name-appeared the `Version` property is read first; a schema other
//   than hudmodel.SCHEMA_VERSION shows "smartPanel version mismatch" and never fetches.
// - Rate: GetState once the version is known good, then on `Changed` at most every 5 s (SP-2301);
//   `settings` changes are fetched at once so the toggle feels instant.
// - All formatting lives in hudmodel.js (pure, unit-tested); this file builds actors.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Pango from 'gi://Pango';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {
    HUD_SECTIONS,
    HUD_WIDTH,
    alignFor,
    buildHudModel,
    buildMismatchModel,
    buildOfflineModel,
    hudSettings,
    modelKey,
    placeHud,
    SCHEMA_VERSION,
} from './hudmodel.js';
import {RefreshThrottle} from './throttle.js';

export const BUS_NAME = 'dev.omdev.SmartPanel';
export const OBJECT_PATH = '/dev/omdev/SmartPanel';
export const INTERFACE = 'dev.omdev.SmartPanel1';
const CALL_TIMEOUT_MS = 3000;

// Indirection so tests can capture warnings (console.warn itself is read-only in gjs).
export const logger = {warn: msg => console.warn(msg)};

// Header letter-spacing (prototype .hl: .2em). It goes in Pango markup, not CSS:
// St does not count CSS letter-spacing when it measures a label, so a CSS-spaced line
// gets ellipsized ("CLAUDE · TOD…", seen in a real GNOME Shell 50 run). In Pango units
// of 1/1024 pt: 1.5 pt ≈ 2 px.
const HEADER_LETTER_SPACING = 1536;
const BIG_LETTER_SPACING = -768;      // prototype .hbig: -.02em ≈ -1 px

function label(styleClass, extra = {}) {
    return new St.Label({style_class: styleClass, reactive: false, ...extra});
}

/** Letter-spaced label text, via markup so St measures what it paints. */
function setSpaced(lbl, text, spacing) {
    const escaped = GLib.markup_escape_text(text, -1);
    lbl.clutter_text.set_markup(`<span letter_spacing="${spacing}">${escaped}</span>`);
}

/** The actor tree. Knows nothing about D-Bus; render() takes a hudmodel model. */
export class HudView {
    constructor() {
        this.actor = new St.BoxLayout({
            style_class: 'smartpanel-hud',
            orientation: Clutter.Orientation.VERTICAL,
            reactive: false,
            can_focus: false,
            track_hover: false,
            visible: false,
            width: HUD_WIDTH,
        });
        this.actor.set_accessible_name('smartPanel desktop HUD');

        this._label = label('smartpanel-hud-label');
        this._big = label('smartpanel-hud-big');
        this._sub = label('smartpanel-hud-label smartpanel-hud-sub');
        // Headers never ellipsize; the HUD widens for them instead (see width()).
        for (const l of [this._label, this._big, this._sub])
            l.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
        this._rows = new St.BoxLayout({
            style_class: 'smartpanel-hud-rows',
            orientation: Clutter.Orientation.VERTICAL,
            reactive: false,
        });
        this._metrics = new St.BoxLayout({
            style_class: 'smartpanel-hud-rows smartpanel-hud-metrics',
            orientation: Clutter.Orientation.VERTICAL,
            reactive: false,
        });
        this._alert = label('smartpanel-hud-alert');
        this._message = label('smartpanel-hud-offline');
        for (const a of [this._label, this._big, this._sub, this._rows, this._metrics,
            this._alert, this._message])
            this.actor.add_child(a);

        this._key = null;
        this.model = null;
    }

    /** Returns true when the picture changed (callers re-place it then). */
    render(model) {
        const key = modelKey(model);
        if (key === this._key)
            return false;
        this._key = key;
        this.model = model;

        this.actor.visible = model.visible;
        if (!model.visible)
            return true;

        const xAlign = alignFor(model.corner) === 'left'
            ? Clutter.ActorAlign.START : Clutter.ActorAlign.END;
        for (const l of [this._label, this._big, this._sub, this._alert, this._message])
            l.x_align = xAlign;

        const online = !model.offline;
        this._label.visible = true;
        // upper-cased here: the prototype's .hl has text-transform, St has none
        setSpaced(this._label, model.label.toUpperCase(), HEADER_LETTER_SPACING);
        this._big.visible = online;
        setSpaced(this._big, model.headline, BIG_LETTER_SPACING);
        this._sub.visible = online;
        setSpaced(this._sub, model.sub.toUpperCase(), HEADER_LETTER_SPACING);

        this._fillRows(this._rows, online ? model.rows : [],
            r => [r.left, r.right, r.style]);
        this._fillRows(this._metrics, online ? model.metrics : [],
            m => [m.label, m.value, m.style]);

        this._alert.visible = online && Boolean(model.alert);
        this._alert.text = model.alert ?? '';
        this._message.visible = Boolean(model.message);
        this._message.text = model.message ?? '';
        return true;
    }

    /**
     * HUD_WIDTH (the prototype's 240 px), or wider when a header line needs it, e.g.
     * "USD · 41.2M TOKENS · 3.10/H" in a large system font. Session titles ellipsize
     * inside that width rather than widening it.
     */
    width() {
        let w = HUD_WIDTH;
        for (const l of [this._label, this._big, this._sub]) {
            if (l.visible)
                w = Math.max(w, Math.ceil(l.get_preferred_width(-1)[1]));
        }
        return w;
    }

    _fillRows(box, items, unpack) {
        box.destroy_all_children();
        box.visible = items.length > 0;
        for (const item of items) {
            const [left, right, style] = unpack(item);
            const row = new St.BoxLayout({style_class: 'smartpanel-hud-row', reactive: false});
            const l = label('smartpanel-hud-row-left', {text: left, x_expand: true});
            l.clutter_text.ellipsize = Pango.EllipsizeMode.END;
            const rClass = style && style !== 'none' && style !== 'more'
                ? `smartpanel-hud-row-right smartpanel-hud-${style}` : 'smartpanel-hud-row-right';
            const r = label(rClass, {text: right, x_align: Clutter.ActorAlign.END});
            row.add_child(l);
            row.add_child(r);
            box.add_child(row);
        }
    }

    destroy() {
        this.actor.destroy();
        this.actor = null;
    }
}

export default class SmartPanelHudExtension extends Extension {
    enable() {
        this._view = new HudView();
        this._group = Main.layoutManager._backgroundGroup;
        this._group.add_child(this._view.actor);
        this._raise();
        this._relayoutId = 0;
        // Sizes are only final once St has styled and allocated the tree; a bottom
        // corner depends on the height, so re-place whenever it settles.
        this._view.actor.connect('notify::height', () => this._queueRelayout());

        this._settings = null;      // last hudSettings() we heard; null == never
        this._online = false;       // name owned AND its Version checked out
        this._generation = 0;       // bumped per appear/vanish: drops stale Version replies
        this._cancellable = new Gio.Cancellable();
        this._throttle = new RefreshThrottle({
            fetch: done => this._fetch(done),
            now: () => GLib.get_monotonic_time() / 1000,
            schedule: (fn, ms) => GLib.timeout_add(GLib.PRIORITY_DEFAULT,
                Math.max(1, Math.ceil(ms)), () => {
                    fn();
                    return GLib.SOURCE_REMOVE;
                }),
            cancel: id => GLib.source_remove(id),
        });

        this._monitorsId = Main.layoutManager.connect('monitors-changed',
            () => this._relayout());
        this._workareasId = global.display.connect('workareas-changed',
            () => this._relayout());

        const bus = Gio.DBus.session;
        this._changedId = bus.signal_subscribe(BUS_NAME, INTERFACE, 'Changed',
            OBJECT_PATH, null, Gio.DBusSignalFlags.NONE,
            (_conn, _sender, _path, _iface, _signal, params) => {
                let sections = [];
                try {
                    sections = params.deepUnpack()[0];
                } catch {}
                this._onChanged(sections);
            });
        this._watchId = Gio.bus_watch_name(Gio.BusType.SESSION, BUS_NAME,
            Gio.BusNameWatcherFlags.NONE,
            () => this._onAppeared(),
            () => this._onVanished());
    }

    disable() {
        if (this._watchId) {
            Gio.bus_unwatch_name(this._watchId);
            this._watchId = 0;
        }
        if (this._changedId) {
            Gio.DBus.session.signal_unsubscribe(this._changedId);
            this._changedId = 0;
        }
        this._cancellable?.cancel();
        this._cancellable = null;
        this._throttle?.reset();
        this._throttle = null;
        if (this._monitorsId) {
            Main.layoutManager.disconnect(this._monitorsId);
            this._monitorsId = 0;
        }
        if (this._workareasId) {
            global.display.disconnect(this._workareasId);
            this._workareasId = 0;
        }
        if (this._relayoutId) {
            GLib.source_remove(this._relayoutId);
            this._relayoutId = 0;
        }
        this._view?.destroy();
        this._view = null;
        this._group = null;
        this._settings = null;
        this._online = false;
    }

    // ── D-Bus ──

    _onAppeared() {
        this._online = false;
        this._throttle.reset();
        const generation = ++this._generation;
        Gio.DBus.session.call(BUS_NAME, OBJECT_PATH, 'org.freedesktop.DBus.Properties', 'Get',
            new GLib.Variant('(ss)', [INTERFACE, 'Version']), new GLib.VariantType('(v)'),
            Gio.DBusCallFlags.NO_AUTO_START, CALL_TIMEOUT_MS, this._cancellable,
            (conn, res) => {
                let version = null;
                try {
                    version = conn.call_finish(res).deepUnpack()[0].deepUnpack();
                } catch (e) {
                    if (e instanceof GLib.Error &&
                        e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                        return;
                    // Unreadable is not proof of a mismatch (a slow or busy service):
                    // fetch anyway; a schema change would still show as a real Version.
                    logger.warn(`smartpanel-hud: reading Version failed: ${e.message}`);
                }
                if (!this._view || !this._throttle || generation !== this._generation)
                    return;
                if (version !== null && version !== SCHEMA_VERSION) {
                    logger.warn(`smartpanel-hud: service schema ${version}, HUD expects ${SCHEMA_VERSION}`);
                    this._render(buildMismatchModel(this._settings));
                    return;
                }
                this._online = true;
                this._throttle.request({urgent: true});
            });
    }

    _onVanished() {
        this._generation++;
        this._online = false;
        this._throttle.reset();
        this._render(buildOfflineModel(this._settings));
    }

    _onChanged(sections) {
        if (!this._online || !this._throttle)
            return;
        const urgent = Array.isArray(sections) && sections.includes('settings');
        // Hidden HUD: only a settings change can make it matter again. While the
        // settings are still unknown (first GetState failed or not back yet), every
        // Changed is a chance to learn them, so let it through.
        if (!urgent && this._settings !== null && !this._settings.enabled)
            return;
        this._throttle.request({urgent});
    }

    _fetch(done) {
        Gio.DBus.session.call(BUS_NAME, OBJECT_PATH, INTERFACE, 'GetState',
            new GLib.Variant('(as)', [HUD_SECTIONS]), new GLib.VariantType('(s)'),
            Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, this._cancellable,
            (conn, res) => {
                try {
                    const [json] = conn.call_finish(res).deepUnpack();
                    this._apply(JSON.parse(json));
                } catch (e) {
                    if (!(e instanceof GLib.Error &&
                          e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))) {
                        logger.warn(`smartpanel-hud: GetState failed: ${e.message}`);
                        // Never heard the settings: without a retry a HUD the user
                        // turned on could stay blank until the next Changed. Queue a
                        // throttled (non-urgent) retry; it replays after done().
                        if (this._settings === null && this._online)
                            this._throttle?.request();
                    }
                } finally {
                    done();
                }
            });
    }

    _apply(state) {
        if (!this._view || !this._online)
            return;
        this._settings = hudSettings(state);
        this._render(buildHudModel(state));
    }

    // ── drawing ──

    _render(model) {
        if (!this._view)
            return;
        if (this._view.render(model)) {
            this._relayout();
            this._queueRelayout();
        }
    }

    /** Re-place once after the current frame's styling/allocation (deduplicated). */
    _queueRelayout() {
        if (this._relayoutId || !this._view)
            return;
        this._relayoutId = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._relayoutId = 0;
            this._relayout();
            return GLib.SOURCE_REMOVE;
        });
    }

    _raise() {
        // New wallpaper actors are lowered to the bottom of the group, but keep the HUD
        // explicitly on top of them after any monitor / background change.
        if (this._view && this._group)
            this._group.set_child_above_sibling(this._view.actor, null);
    }

    _relayout() {
        const view = this._view;
        if (!view?.model?.visible)
            return;
        const index = Main.layoutManager.primaryIndex;
        if (index < 0) {
            view.actor.hide();
            return;
        }
        view.actor.show();
        this._raise();
        const area = Main.layoutManager.getWorkAreaForMonitor(index);
        const width = view.width();
        if (view.actor.width !== width)
            view.actor.width = width;
        const [, height] = view.actor.get_preferred_height(width);
        const {x, y} = placeHud(
            {x: area.x, y: area.y, width: area.width, height: area.height},
            {width, height},
            view.model.corner, view.model.margin);
        view.actor.set_position(x, y);
    }
}
