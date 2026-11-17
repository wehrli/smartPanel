#!/usr/bin/env bash
# Install the smartPanel desktop HUD (GNOME Shell extension smartpanel-hud@omdev.dev)
# for the current user.
#
#   ./scripts/install-hud.sh            pack + install (+ enable if the shell knows it)
#   ./scripts/install-hud.sh --pack     only build the .zip into dist/
#   ./scripts/install-hud.sh --dry-run  print what would run, change nothing
#
# The HUD is optional: smartPanel works without it. It needs the smartPanel app
# running (it reads dev.omdev.SmartPanel over D-Bus) and `hud.enabled: true`, which
# you can switch from the bar menu, Settings, the palette or `smartpanel --hud toggle`.
set -euo pipefail

UUID="smartpanel-hud@omdev.dev"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/extension/$UUID"
OUT="$ROOT/dist"

MODE=install
case "${1:-}" in
  "") ;;
  --pack) MODE=pack ;;
  --dry-run|-n) MODE=dry ;;
  -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
esac

run() {
  if [[ $MODE == dry ]]; then
    printf '+'; printf ' %q' "$@"; printf '\n'
  else
    "$@"
  fi
}

if ! command -v gnome-extensions >/dev/null 2>&1; then
  echo "gnome-extensions not found: this needs GNOME Shell (package gnome-shell)." >&2
  exit 1
fi
for f in metadata.json extension.js hudmodel.js throttle.js stylesheet.css; do
  [[ -f "$SRC/$f" ]] || { echo "missing $SRC/$f" >&2; exit 1; }
done

shell_version="$(gnome-shell --version 2>/dev/null | awk '{print $3}' | cut -d. -f1 || true)"
if [[ -n "$shell_version" && "$shell_version" != "50" ]]; then
  echo "warning: this extension targets GNOME Shell 50; you run $shell_version." >&2
  echo "         GNOME will refuse to load it unless metadata.json lists your version." >&2
fi

run mkdir -p "$OUT"
# extension.js, metadata.json and stylesheet.css are packed by default; the two
# plain-JS modules it imports must be named explicitly.
run gnome-extensions pack "$SRC" --force --out-dir="$OUT" \
  --extra-source=hudmodel.js --extra-source=throttle.js
ZIP="$OUT/$UUID.shell-extension.zip"

if [[ $MODE == pack ]]; then
  echo "Built $ZIP"
  exit 0
fi

run gnome-extensions install --force "$ZIP"

if [[ $MODE == dry ]]; then
  echo
  echo "(dry run: nothing was built or installed)"
else
  echo
  echo "Installed $UUID for $(id -un)."
fi

cat <<EOF

Next steps:
  1. Log out and back in. On Wayland GNOME Shell only discovers new extensions at
     login (on X11, Alt+F2 then 'r' also works).
  2. Enable it:   gnome-extensions enable $UUID
     (or in the Extensions app).
  3. Turn the HUD on in smartPanel: bar menu "Desktop HUD", Settings, or
     smartpanel --hud toggle. The corner is set in Settings (hud.corner).

Remove it with:  gnome-extensions uninstall $UUID
EOF

# Already known to this shell (re-install / X11 reload): enable now, harmless otherwise.
if [[ $MODE == install ]] && gnome-extensions info "$UUID" >/dev/null 2>&1; then
  gnome-extensions enable "$UUID" 2>/dev/null && echo "(enabled $UUID)" || true
fi
