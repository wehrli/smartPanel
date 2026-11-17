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
// - Rate: GetState on name-appeared, then on `Changed` at most every 5 s (SP-2301);
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
    buildOfflineModel,
    hudSettings,
    modelKey,
    placeHud,
} from './hudmodel.js';
import {RefreshThrottle} from './throttle.js';

export const BUS_NAME = 'dev.omdev.SmartPanel';
export const OBJECT_PATH = '/dev/omdev/SmartPanel';
export const INTERFACE = 'dev.omdev.SmartPanel1';
const CALL_TIMEOUT_MS = 3000;

// Indirection so tests can capture warnings (console.warn itself is read-only in gjs).
export const logger = {warn: msg => console.warn(msg)};

function label(styleClass, extra = {}) {
    return new St.Label({style_class: styleClass, reactive: false, ...extra});
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
        this._label.text = model.label;
        this._big.visible = online;
        this._big.text = model.headline;
        this._sub.visible = online;
        this._sub.text = model.sub;

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

        this._settings = null;      // last hudSettings() we heard; null == never
        this._online = false;
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
        this._view?.destroy();
        this._view = null;
        this._group = null;
        this._settings = null;
        this._online = false;
    }

    // ── D-Bus ──

    _onAppeared() {
        this._online = true;
        this._throttle.reset();
        this._throttle.request({urgent: true});
    }

    _onVanished() {
        this._online = false;
        this._throttle.reset();
        this._render(buildOfflineModel(this._settings));
    }

    _onChanged(sections) {
        if (!this._online || !this._throttle)
            return;
        const urgent = Array.isArray(sections) && sections.includes('settings');
        // Hidden HUD: only a settings change can make it matter again.
        if (!urgent && !this._settings?.enabled)
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
                          e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED)))
                        logger.warn(`smartpanel-hud: GetState failed: ${e.message}`);
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
        if (this._view.render(model))
            this._relayout();
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
        const [, height] = view.actor.get_preferred_height(HUD_WIDTH);
        const {x, y} = placeHud(
            {x: area.x, y: area.y, width: area.width, height: area.height},
            {width: HUD_WIDTH, height},
            view.model.corner, view.model.margin);
        view.actor.set_position(x, y);
    }
}
