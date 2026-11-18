#!/usr/bin/env bash
# Capture the three SP-0002 top-bar rendering options in a real GNOME Shell panel
# (ADR docs/adr/0002-top-bar-graphics.md):
#
#   wide     IconName = absolute path to a wide PNG   (what ships, D2)
#   pixmap   the same image as IconPixmap             (forced square by the host)
#   unicode  label only: block-char histogram + strip (the text fallback)
#
#   scripts/capture_bar_options.sh [out_dir]      default: docs/adr/img
#
# Runs a throwaway headless GNOME Shell (Wayland, virtual monitor) on a private
# dbus-run-session bus with the stock ubuntu-appindicators extension, HOME/XDG dirs
# in a temp dir and the keyfile GSettings backend. Nothing is installed or enabled for
# the real user and the running session is not touched. Takes ~15 s.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$(mkdir -p "${1:-$ROOT/docs/adr/img}" && cd "${1:-$ROOT/docs/adr/img}" && pwd)"
PY="${PYTHON:-python3}"
APPIND="ubuntu-appindicators@ubuntu.com"
[[ -d "/usr/share/gnome-shell/extensions/$APPIND" ]] || { echo "needs $APPIND" >&2; exit 1; }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/bar-options-XXXXXX")"
RT="$(mktemp -d /tmp/bro-XXXXXX)"         # short: wayland socket paths are capped at 108 bytes
trap 'rm -rf -- "$RT" "$WORK"' EXIT
chmod 700 "$RT"
EXTDIR="$WORK/data/gnome-shell/extensions/probe@bar.local"
mkdir -p "$EXTDIR" "$WORK/config/glib-2.0/settings" "$WORK/home" "$WORK/cache" "$WORK/shots"

cat > "$EXTDIR/metadata.json" <<'EOF'
{"uuid": "probe@bar.local", "name": "probe", "description": "panel capture", "shell-version": ["50"]}
EOF
cat > "$EXTDIR/extension.js" <<EOF
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
const OUT = '$WORK/shots';
function shoot(name, x, y, w, h) {
    const f = Gio.File.new_for_path(OUT + '/' + name + '.png').replace(null, false, 0, null);
    new Shell.Screenshot().screenshot_area(x, y, w, h, f, (o, r) => {
        o.screenshot_area_finish(r);
        f.close(null);
    });
}
export default class Probe extends Extension {
    enable() {
        this._id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 7000, () => {
            this._id = 0;
            Main.overview.hide();
            GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1500, () => {
                let log = '';
                for (const [key, actor] of Object.entries(Main.panel.statusArea)) {
                    const m = key.match(/\/opt\/(\w+)/);
                    log += key + '\n';
                    if (!m)
                        continue;
                    const [x, y] = actor.get_transformed_position();
                    const [w, h] = actor.get_transformed_size();
                    log += '  ' + m[1] + ' ' + [x, y, w, h].join(',') + '\n';
                    shoot(m[1], Math.floor(x) - 6, Math.floor(y), Math.ceil(w) + 12, Math.ceil(h));
                }
                GLib.file_set_contents(OUT + '/status.txt', log);
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
enabled-extensions=['$APPIND', 'probe@bar.local']
disable-user-extensions=false
EOF

# Three SNI items on one connection, at /opt/<name>, exported with the app's own
# StatusNotifierItem and drawn with its own barrender (demo "busy" scenario).
cat > "$WORK/items.py" <<EOF
import sys
sys.path.insert(0, '$ROOT')
import cairo
from gi.repository import Gio, GLib
from smartpanel import demo
from smartpanel.tray import barrender
from smartpanel.tray.barspec import compose_bar
from smartpanel.tray.sni import StatusNotifierItem

spec = compose_bar(demo.build_state('busy'), {'bar.ticker': False})
png = '$RT/smartpanel/bar-spike.png'
import os; os.makedirs(os.path.dirname(png), exist_ok=True)
open(png, 'wb').write(barrender.render_png(spec.icon))

surf = cairo.ImageSurface.create_from_png(png)
w, h, stride, data = surf.get_width(), surf.get_height(), surf.get_stride(), bytes(surf.get_data())
argb = bytearray()                       # SNI pixmaps: ARGB32, network byte order
for y in range(h):
    for x in range(w):
        b, g, r, a = data[y * stride + 4 * x: y * stride + 4 * x + 4]
        un = (lambda c: min(255, c * 255 // a)) if a else (lambda c: 0)
        argb += bytes((a, un(r), un(g), un(b)))

items = []
def add(name, icon, label=''):
    it = StatusNotifierItem('/opt/' + name, item_id='opt-' + name, title=name, icon_name=icon)
    if label:
        it.props['XAyatanaLabel'] = label
        it.props['XAyatanaLabelGuide'] = label
    return it
wide = add('wide', png, spec.label)
pixmap = add('pixmap', '', spec.label)
pixmap.props['IconPixmap'] = [(w, h, bytes(argb))]
unicode = add('unicode', 'utilities-terminal-symbolic', '▁▂▃▅▇ ▮▮▯ ' + spec.label)
items = [wide, pixmap, unicode]
conn = Gio.bus_get_sync(Gio.BusType.SESSION)
for it in items:
    it.export(conn)
GLib.timeout_add_seconds(20, lambda: sys.exit(0))
GLib.MainLoop().run()
EOF
cat > "$WORK/run.sh" <<EOF
#!/bin/bash
timeout 20 gnome-shell --headless --wayland --no-x11 --virtual-monitor 1600x900 \
    --wayland-display=bar-options > '$WORK/shell.log' 2>&1 & shell=\$!
sleep 3
timeout 18 '$PY' '$WORK/items.py' > '$WORK/items.log' 2>&1 & items=\$!
wait \$shell 2>/dev/null
kill \$items 2>/dev/null; wait \$items 2>/dev/null
exit 0
EOF
chmod +x "$WORK/run.sh"

env -i PATH=/usr/bin:/bin HOME="$WORK/home" XDG_DATA_HOME="$WORK/data" \
    XDG_CONFIG_HOME="$WORK/config" XDG_CACHE_HOME="$WORK/cache" XDG_RUNTIME_DIR="$RT" \
    GSETTINGS_BACKEND=keyfile LANG=C.UTF-8 \
    timeout 40 dbus-run-session -- "$WORK/run.sh" >/dev/null 2>&1 || true

cat "$WORK/shots/status.txt" 2>/dev/null || { echo "no capture; shell log:" >&2; tail -20 "$WORK/shell.log" >&2; cat "$WORK/items.log" >&2; exit 1; }
for name in wide pixmap unicode; do
  if [[ -s "$WORK/shots/$name.png" ]]; then
    cp "$WORK/shots/$name.png" "$OUT/0002-$name.png"
    echo "ok   $OUT/0002-$name.png"
  else
    echo "MISSING $name (item not shown?)" >&2
  fi
done
