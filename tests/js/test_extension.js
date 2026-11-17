// Checks extension.js without a running GNOME Shell.
// Run: gjs -m tests/js/test_extension.js
//
// The extension's sources are copied to a temp dir (SMARTPANEL_HUD_TEST_TMP, else a
// fresh GLib temp dir) with every shell/GI import rewritten to the fakes in ./stubs.
// Importing the copy is the syntax + link check (a typo'd named import fails here);
// then the real enable()/disable() lifecycle is driven against a fake D-Bus service.
//
// Any import the rewrite map doesn't know fails the test, so a new shell dependency
// must come with a stub — and hudmodel.js / throttle.js must have no such imports.

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';

import {dirOf, eq, ok, readJson, readText, run, test} from './harness.js';

const HERE = dirOf(import.meta.url);
// SMARTPANEL_HUD_EXT_DIR lets the pytest wrapper prove a broken copy fails this check.
const SRC = GLib.getenv('SMARTPANEL_HUD_EXT_DIR') ?? `${HERE}/../../extension/smartpanel-hud@omdev.dev`;
const FIX = `${HERE}/../fixtures/hud`;

const REWRITE = {
    'gi://Clutter': './stubs/clutter.js',
    'gi://Gio': './stubs/gio.js',
    'gi://GLib': './stubs/glib.js',
    'gi://Pango': './stubs/pango.js',
    'gi://St': './stubs/st.js',
    'resource:///org/gnome/shell/extensions/extension.js': './stubs/extension.js',
    'resource:///org/gnome/shell/ui/main.js': './stubs/main.js',
};
const PURE = ['hudmodel.js', 'throttle.js'];
const IMPORT_RE = /(\bfrom\s+|\bimport\s+|\bimport\s*\(\s*)(['"])([^'"]+)\2/g;

function listDir(path) {
    const names = [];
    const en = Gio.File.new_for_path(path).enumerate_children('standard::name',
        Gio.FileQueryInfoFlags.NONE, null);
    let info;
    while ((info = en.next_file(null)))
        names.push(info.get_name());
    return names.sort();
}

function write(path, text) {
    GLib.file_set_contents(path, new TextEncoder().encode(text));
}

function stage() {
    const base = GLib.getenv('SMARTPANEL_HUD_TEST_TMP') || GLib.dir_make_tmp('smartpanel-hud-XXXXXX');
    const dir = `${base}/ext-${GLib.get_monotonic_time()}`;
    GLib.mkdir_with_parents(`${dir}/stubs`, 0o755);
    const unknown = [];
    for (const name of listDir(SRC)) {
        let text = readText(`${SRC}/${name}`);
        if (name.endsWith('.js')) {
            text = text.replace(IMPORT_RE, (all, kw, q, spec) => {
                if (spec.startsWith('./'))
                    return all;
                if (PURE.includes(name) || !(spec in REWRITE)) {
                    unknown.push(`${name}: ${spec}`);
                    return all;
                }
                return `${kw}${q}${REWRITE[spec]}${q}`;
            });
        }
        write(`${dir}/${name}`, text);
    }
    for (const name of listDir(`${HERE}/stubs`))
        write(`${dir}/stubs/${name}`, readText(`${HERE}/stubs/${name}`));
    return {dir, unknown};
}

const {dir, unknown} = stage();
const fixtureJson = name => JSON.stringify(readJson(`${FIX}/${name}.json`));

let mod, fakeMod, st, mainMod;
async function load() {
    if (mod)
        return;
    fakeMod = await import(`file://${dir}/stubs/fake.js`);
    st = await import(`file://${dir}/stubs/st.js`);
    mainMod = await import(`file://${dir}/stubs/main.js`);
    globalThis.global = {display: new fakeMod.Signals()};
    mod = await import(`file://${dir}/extension.js`);
}

// ── helpers that play the smartPanel service ──

function newExtension() {
    fakeMod.resetFake();
    mainMod.layoutManager.reset();
    globalThis.global = {display: new fakeMod.Signals()};
    const meta = readJson(`${SRC}/metadata.json`);
    const ext = new mod.default({...meta, path: dir});
    ext.enable();
    return ext;
}

const fake = () => fakeMod.fake;
const watch = () => [...fake().watches.values()][0];
const changed = sections => {
    for (const s of fake().subscriptions.values())
        s.cb(null, ':1.1', s.path, s.iface, s.member, new GLib.Variant('(as)', [sections]));
};
const hud = () => mainMod.layoutManager._backgroundGroup.get_children()
    .find(c => c.style_class === 'smartpanel-hud');
const texts = actor => {
    const out = [];
    const walk = a => {
        if (!a.visible)
            return;
        if (a instanceof st.Label)
            out.push(a.text);
        a.children.forEach(walk);
    };
    walk(actor);
    return out;
};
const replyWith = name => {
    eq(fake().calls.length, 1, 'one GetState in flight');
    fake().calls[0].reply(fixtureJson(name));
};

// ── static checks ──

test('extension sources import only known shell modules; pure modules import nothing', () => {
    eq(unknown, []);
});

test('extension.js parses and links (all named imports exist)', async () => {
    await load();
    ok(typeof mod.default === 'function', 'default export is the Extension class');
    ok(typeof mod.HudView === 'function');
    eq(mod.BUS_NAME, 'dev.omdev.SmartPanel');
    eq(mod.OBJECT_PATH, '/dev/omdev/SmartPanel');
    eq(mod.INTERFACE, 'dev.omdev.SmartPanel1');
});

test('D-Bus names match smartpanel/dbus/panel1.xml', async () => {
    await load();
    const xml = readText(`${HERE}/../../smartpanel/dbus/panel1.xml`);
    ok(xml.includes(`<interface name="${mod.INTERFACE}">`));
    ok(xml.includes(`Bus name: ${mod.BUS_NAME}`) && xml.includes(`Object path: ${mod.OBJECT_PATH}`));
    ok(/<method name="GetState">\s*<arg type="as"[^>]*direction="in"\/>[\s\S]*?<arg type="s"[^>]*direction="out"/.test(xml),
        'GetState(as) -> s');
    ok(/<signal name="Changed"><arg type="as"/.test(xml));
});

test('metadata.json targets GNOME Shell 50', () => {
    const meta = readJson(`${SRC}/metadata.json`);
    eq(meta.uuid, 'smartpanel-hud@omdev.dev');
    eq(meta.name, 'smartPanel HUD');
    eq(meta['shell-version'], ['50']);
    ok(meta.description.length > 20);
});

// ── lifecycle against a fake service ──

test('enable: invisible actor on top of the wallpaper, watching the bus name', async () => {
    await load();
    const ext = newExtension();
    const group = mainMod.layoutManager._backgroundGroup;
    eq(group.get_children().map(c => c.name ?? c.style_class), ['wallpaper', 'smartpanel-hud']);
    eq(hud().visible, false);
    eq(watch().name, 'dev.omdev.SmartPanel');
    const sub = [...fake().subscriptions.values()][0];
    eq([sub.sender, sub.iface, sub.member, sub.path],
        ['dev.omdev.SmartPanel', 'dev.omdev.SmartPanel1', 'Changed', '/dev/omdev/SmartPanel']);
    eq(fake().calls.length, 0, 'no call before the name appears');
    ext.disable();
});

test('name appears: GetState for the HUD sections, then the busy HUD is drawn top-right', async () => {
    await load();
    const ext = newExtension();
    watch().appeared();
    const call = fake().calls[0];
    eq(call.method, 'GetState');
    eq(call.params.deepUnpack(), [['spend', 'sessions', 'system', 'settings', 'app']]);
    eq(call.replyType.dup_string(), '(s)');
    replyWith('busy');
    const a = hud();
    eq(a.visible, true);
    eq(texts(a), [
        'CLAUDE · TODAY', '23.84', 'usd · 41.2M tokens · 3.10/h',
        'Fix swap alert hysteresis', 'needs you',
        'Truck search filters', 'running',
        'Stripe webhook retries', 'running',
        'Migrate goose 0023', 'idle',
        'cpu', '23%', 'memory', '68%', 'swap', '12%', 'net ↓', '2.4 MB/s',
    ]);
    // work area 0,32 1920x1048; margin 30; width 240
    eq([a.x, a.y], [1920 - 30 - 240, 32 + 30]);
    ext.disable();
});

test('state words and swap carry their colour classes; nothing is reactive', async () => {
    await load();
    const ext = newExtension();
    watch().appeared();
    replyWith('many');
    const labels = fake().actors.filter(x => x instanceof st.Label && !x.destroyed);
    const cls = t => labels.find(l => l.text === t)?.style_class;
    ok(cls('needs you').includes('smartpanel-hud-waiting'));
    ok(cls('running').includes('smartpanel-hud-running'));
    ok(cls('journaling').includes('smartpanel-hud-journaling'));
    ok(cls('starting').includes('smartpanel-hud-launching'));
    ok(cls('93%').includes('smartpanel-hud-crit'));
    eq(texts(hud()).slice(-1), ['⚠ swap critical']);
    ok(texts(hud()).includes('+3 more'));
    eq(fake().actors.filter(x => x.reactive !== false).length, 0, 'every actor non-reactive');
    // bottom-left with margin 48: left aligned, bottom edge 48 px above the work area's
    const a = hud();
    const [, h] = a.get_preferred_height(240);
    eq([a.x, a.y], [48, 32 + 1048 - 48 - h]);
    ext.disable();
});

test('Changed is throttled to one GetState per 5 s; settings changes go out at once', async () => {
    await load();
    const ext = newExtension();
    watch().appeared();
    replyWith('busy');
    fakeMod.advance(1000);
    changed(['system']);
    changed(['sessions']);
    eq(fake().calls.length, 0, 'inside the 5 s window');
    fakeMod.advance(3999);
    eq(fake().calls.length, 0);
    fakeMod.advance(1);
    eq(fake().calls.length, 1, 'one trailing fetch at +5 s');
    replyWith('busy');
    changed(['settings']);
    eq(fake().calls.length, 1, 'settings bypasses the throttle');
    replyWith('busy');
    ext.disable();
});

test('hud.enabled false hides it and ignores non-settings changes; true shows it again (SP-2303)', async () => {
    await load();
    const ext = newExtension();
    watch().appeared();
    replyWith('busy');
    changed(['settings']);
    replyWith('disabled');
    eq(hud().visible, false);
    fakeMod.advance(10000);
    changed(['system', 'spend']);
    fakeMod.advance(10000);
    eq(fake().calls.length, 0, 'hidden HUD does not poll');
    changed(['settings']);
    replyWith('busy');
    eq(hud().visible, true);
    ext.disable();
});

test('corner change re-places the HUD (SP-2304)', async () => {
    await load();
    const ext = newExtension();
    watch().appeared();
    const s = readJson(`${FIX}/busy.json`);
    s.settings.values['hud.corner'] = 'top-left';
    s.settings.values['hud.margin'] = 10;
    fake().calls[0].reply(JSON.stringify(s));
    eq([hud().x, hud().y], [10, 42]);
    ext.disable();
});

test('monitors-changed / workareas-changed re-layout on the primary monitor', async () => {
    await load();
    const ext = newExtension();
    watch().appeared();
    replyWith('busy');
    const lm = mainMod.layoutManager;
    lm.workAreas = [{x: 0, y: 0, width: 1280, height: 1024}, {x: 1280, y: 27, width: 2560, height: 1413}];
    lm.primaryIndex = 1;
    lm.emit('monitors-changed');
    eq([hud().x, hud().y], [1280 + 2560 - 30 - 240, 27 + 30]);
    lm.workAreas[1] = {x: 1280, y: 27, width: 2000, height: 1413};
    global.display.emit('workareas-changed');
    eq(hud().x, 1280 + 2000 - 30 - 240);
    // a new wallpaper actor lands on top; the next relayout puts the HUD back above it
    lm._backgroundGroup.add_child(new st.Widget({name: 'wallpaper2'}));
    lm.emit('monitors-changed');
    eq(lm._backgroundGroup.get_children().slice(-1)[0], hud());
    lm.primaryIndex = -1;
    lm.emit('monitors-changed');
    eq(hud().visible, false, 'no primary monitor: hidden');
    ext.disable();
});

test('name vanishes: dim "smartPanel not running" when it was on, nothing when off', async () => {
    await load();
    let ext = newExtension();
    watch().appeared();
    replyWith('busy');
    watch().vanished();
    eq(texts(hud()), ['CLAUDE · TODAY', 'smartPanel not running']);
    // service comes back
    watch().appeared();
    replyWith('busy');
    eq(texts(hud())[1], '23.84');
    ext.disable();

    ext = newExtension();
    watch().vanished();               // never heard from it: we don't know hud.enabled
    eq(hud().visible, false);
    ext.disable();
});

test('Changed while vanished is ignored; a reply after vanish is dropped', async () => {
    await load();
    const ext = newExtension();
    watch().appeared();
    const call = fake().calls[0];
    watch().vanished();
    call.reply(fixtureJson('busy'));
    ok(!texts(hud()).includes('23.84'), 'stale reply not drawn');
    changed(['settings']);
    eq(fake().calls.length, 0);
    ext.disable();
});

test('GetState errors are logged, never thrown; the throttle keeps working', async () => {
    await load();
    const ext = newExtension();
    const warn = mod.logger.warn;
    const logged = [];
    mod.logger.warn = m => logged.push(String(m));
    try {
        watch().appeared();
        fake().calls[0].fail(new GLib.Error(Gio.DBusError, Gio.DBusError.SERVICE_UNKNOWN, 'gone'));
        eq(logged.length, 1);
        changed(['settings']);
        fake().calls[0].reply('{not json');
        eq(logged.length, 2);
        changed(['settings']);
        fake().calls[0].fail(new GLib.Error(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED, 'x'));
        eq(logged.length, 2, 'cancellation is silent');
        changed(['settings']);
        eq(fake().calls.length, 1, 'still fetching after errors');
        fake().calls[0].reply(fixtureJson('busy'));
        eq(hud().visible, true);
    } finally {
        mod.logger.warn = warn;
    }
    ext.disable();
});

test('disable: everything torn down, late replies are harmless', async () => {
    await load();
    const ext = newExtension();
    watch().appeared();
    replyWith('busy');
    changed(['system']);              // arms the trailing timer
    eq(fake().timers.size, 1);
    changed(['settings']);            // and one call in flight
    const call = fake().calls[0];
    const cancellable = call.cancellable;
    const actor = hud();
    ext.disable();
    eq(fake().watches.size, 0, 'unwatched');
    eq(fake().subscriptions.size, 0, 'unsubscribed');
    eq(fake().timers.size, 0, 'no timers left');
    ok(cancellable.cancelled, 'in-flight call cancelled');
    ok(actor.destroyed && !mainMod.layoutManager._backgroundGroup.get_children().includes(actor));
    eq(mainMod.layoutManager.handlerCount, 0, 'monitors-changed disconnected');
    eq(global.display.handlerCount, 0, 'workareas-changed disconnected');
    call.reply(fixtureJson('busy'));  // must not throw
    // and it can be enabled again (lock/unlock cycles disable+enable the extension)
    ext.enable();
    watch().appeared();
    replyWith('busy');
    eq(hud().visible, true);
    ext.disable();
});

await run();
