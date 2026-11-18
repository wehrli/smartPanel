#!/usr/bin/env bash
# Manual smoke test: load the HUD extension in a throwaway, headless GNOME Shell and
# screenshot it. NOT run by pytest (it starts a compositor for ~20 s).
#
#   tests/js/headless_shell_smoke.sh [fixture] [service_seconds]
#     fixture          tests/fixtures/hud/<name>.json served by a fake smartPanel (busy)
#     service_seconds  the fake service exits after this long; < 6 shows the
#                      "smartPanel not running" state (default 30)
#
# Fully isolated: private dbus-run-session bus, HOME/XDG dirs in a temp dir, keyfile
# GSettings backend. It does not install or enable anything for the real user and does
# not touch the running session. Output: actor tree + PNG in the printed directory.
set -euo pipefail

FIXTURE="${1:-busy}"
SERVICE_S="${2:-30}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UUID="smartpanel-hud@omdev.dev"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/hud-smoke-XXXXXX")"
RT="$(mktemp -d /tmp/hrt-XXXXXX)"        # short: wayland socket paths are capped at 108 bytes
# gnome-shell fills $RT (at-spi, dbus-1, dconf, gvfs, pulse...), so rmdir can't remove it.
# $WORK stays: it holds the outputs printed below.
trap 'rm -rf -- "$RT"' EXIT
chmod 700 "$RT"
OUT="$WORK/out"
EXTDIR="$WORK/data/gnome-shell/extensions"
mkdir -p "$OUT" "$EXTDIR/probe@smoke.local" "$WORK/config/glib-2.0/settings" "$WORK/home" "$WORK/cache"
cp -r "$ROOT/extension/$UUID" "$EXTDIR/"

cat > "$EXTDIR/probe@smoke.local/metadata.json" <<'EOF'
{"uuid": "probe@smoke.local", "name": "probe", "description": "smoke probe", "shell-version": ["50"]}
EOF
cat > "$EXTDIR/probe@smoke.local/extension.js" <<EOF
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
const OUT = '$OUT';
function dump(a, d = 0) {
    const t = a.text !== undefined ? JSON.stringify(a.text) : '';
    let s = ' '.repeat(d) + (a.style_class || a.constructor.name) + ' vis=' + a.visible +
        ' reactive=' + a.reactive + ' xy=' + a.x + ',' + a.y + ' wh=' + a.width + 'x' + a.height + ' ' + t + '\n';
    for (const c of a.get_children())
        s += dump(c, d + 1);
    return s;
}
export default class Probe extends Extension {
    enable() {
        this._id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 6000, () => {
            this._id = 0;
            Main.overview.hide();
            const hud = Main.extensionManager.lookup('$UUID');
            let s = 'hud state=' + hud?.state + ' error=' + (hud?.error ?? '') + '\n';
            s += dump(Main.layoutManager._backgroundGroup);
            GLib.file_set_contents(OUT + '/tree.txt', s);
            GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1500, () => {
                const f = Gio.File.new_for_path(OUT + '/hud.png').replace(null, false, 0, null);
                new Shell.Screenshot().screenshot(false, f, (o, r) => { o.screenshot_finish(r); f.close(null); });
                return GLib.SOURCE_REMOVE;
            });
            return GLib.SOURCE_REMOVE;
        });
    }
    disable() {
        if (this._id)
            GLib.source_remove(this._id);
    }
}
EOF
cat > "$WORK/config/glib-2.0/settings/keyfile" <<EOF
[org/gnome/shell]
enabled-extensions=['$UUID', 'probe@smoke.local']
disable-user-extensions=false
EOF
cat > "$WORK/service.py" <<EOF
import json, sys
from gi.repository import Gio, GLib
node = Gio.DBusNodeInfo.new_for_xml(open('$ROOT/smartpanel/dbus/panel1.xml').read())
state = json.load(open('$ROOT/tests/fixtures/hud/$FIXTURE.json'))
log = open('$OUT/calls.txt', 'w')
def on_call(conn, sender, path, iface, method, params, inv):
    log.write(f'{method} {params.unpack()}\n'); log.flush()
    inv.return_value(GLib.Variant('(s)', (json.dumps(state),)) if method == 'GetState' else None)
Gio.bus_own_name(Gio.BusType.SESSION, 'dev.omdev.SmartPanel', 0,
    lambda c, n: c.register_object('/dev/omdev/SmartPanel', node.interfaces[0], on_call,
                                   lambda *a: GLib.Variant('u', 1), None), None, None)
GLib.timeout_add_seconds($SERVICE_S, lambda: sys.exit(0))
GLib.MainLoop().run()
EOF
cat > "$WORK/run.sh" <<EOF
#!/bin/bash
python3 '$WORK/service.py' & svc=\$!
sleep 1
timeout 20 gnome-shell --headless --wayland --no-x11 --virtual-monitor 1280x800 \
    --wayland-display=hud-smoke > '$OUT/shell.log' 2>&1
kill \$svc 2>/dev/null; wait \$svc 2>/dev/null
exit 0
EOF
chmod +x "$WORK/run.sh"

env -i PATH=/usr/bin:/bin HOME="$WORK/home" XDG_DATA_HOME="$WORK/data" \
    XDG_CONFIG_HOME="$WORK/config" XDG_CACHE_HOME="$WORK/cache" XDG_RUNTIME_DIR="$RT" \
    GSETTINGS_BACKEND=keyfile LANG=C.UTF-8 \
    timeout 40 dbus-run-session -- "$WORK/run.sh" >/dev/null 2>&1 || true

echo "GetState calls:"; cat "$OUT/calls.txt" 2>/dev/null || echo "  (none)"
echo; cat "$OUT/tree.txt" 2>/dev/null || { echo "no tree dumped; see $OUT/shell.log"; exit 1; }
grep -i "JS ERROR" "$OUT/shell.log" && exit 1
echo; echo "screenshot: $OUT/hud.png"
