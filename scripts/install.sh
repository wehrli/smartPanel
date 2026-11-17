#!/usr/bin/env bash
# Install smartPanel for the current user: a systemd --user service that starts the
# GTK 4 app on login, plus a D-Bus session service so `smartpanel --dashboard` /
# `--palette` (and the HUD extension) can start it on demand.
#
#   ./scripts/install.sh              check deps, install + (re)start the service
#   ./scripts/install.sh --dry-run    print what would be done, change nothing
#   ./scripts/install.sh --no-start   install the files but don't (re)start anything
#   SMARTPANEL_PYTHON=/path/python ./scripts/install.sh   use another interpreter
#
# Runs straight from this source tree with the system python3: no pip, no venv. On
# Ubuntu 24.04+ system-wide pip is blocked (PEP 668), and PyGObject has to come from
# apt anyway, so every dependency is an apt package.
#
# The desktop HUD is a separate, optional GNOME Shell extension:
#   ./scripts/install-hud.sh
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_DIR="$( dirname "$SCRIPT_DIR" )"
PYTHON_BIN="${SMARTPANEL_PYTHON:-$(command -v python3)}"

MODE=install
START=1
for arg in "$@"; do
  case "$arg" in
    --dry-run|-n) MODE=dry ;;
    --no-start) START=0 ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

run() {
  if [[ $MODE == dry ]]; then
    printf '+'; printf ' %q' "$@"; printf '\n'
  else
    "$@"
  fi
}

# Write stdin to a file (or, in a dry run, show where it would go).
put() {
  if [[ $MODE == dry ]]; then
    echo "+ write $1:"; sed 's/^/    /'
  else
    cat > "$1"
  fi
}

APT_PKGS="python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 python3-cairo gir1.2-notify-0.7 python3-psutil python3-yaml tmux terminator"

# ---- 1. hard requirements: the app cannot start without these ----
missing=()
check_py() { "$PYTHON_BIN" -c "$2" >/dev/null 2>&1 || missing+=("$1"); }
check_py "python >= 3.11"          'import sys; assert sys.version_info >= (3, 11)'
check_py "PyGObject + GTK 4"       'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk'
check_py "libadwaita 1"            'import gi; gi.require_version("Adw", "1"); from gi.repository import Adw'
check_py "pycairo (+ gi cairo)"    'import cairo, gi; from gi.repository import cairo as gicairo'
check_py "psutil"                  'import psutil'
check_py "PyYAML"                  'import yaml'

if (( ${#missing[@]} )); then
  echo "smartPanel: missing for $PYTHON_BIN:" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  echo >&2
  echo "Install them with:" >&2
  echo "  sudo apt install $APT_PKGS" >&2
  [[ $MODE == dry ]] || exit 1
  echo "(dry run: carrying on)" >&2
fi

# ---- 2. soft requirements: features degrade without these, the app still runs ----
"$PYTHON_BIN" -c 'import gi; gi.require_version("Notify", "0.7")' >/dev/null 2>&1 \
  || echo "note: libnotify bindings (gir1.2-notify-0.7) not found — notifications fall back to notify-send."
for tool in tmux terminator claude; do
  command -v "$tool" >/dev/null 2>&1 \
    || echo "warning: '$tool' not found — Claude session management won't work until it is installed."
done

# ---- 3. journal-on-close needs a /journal skill; switch it off if there is none ----
journal_env=""
if [[ ! -e "$HOME/.claude/skills/journal/SKILL.md" && ! -e "$HOME/.claude/commands/journal.md" ]]; then
  journal_env='Environment=SMARTPANEL_JOURNAL_SKILL='
  echo "note: no /journal skill in ~/.claude — journal-on-close is disabled (closing a session just closes it)."
fi

# Fill a *.in template with this install's interpreter, tree and journal line.
fill() {
  sed -e "s|@PYTHON@|${PYTHON_BIN}|g" \
      -e "s|@REPO@|${REPO_DIR}|g" \
      -e "s|@JOURNAL_ENV@|${journal_env}|g" "$1"
}

# ---- 4. `smartpanel` and `claude-tmux` on PATH ----
BIN_DIR="$HOME/.local/bin"
run mkdir -p "$BIN_DIR"
run ln -sf "$SCRIPT_DIR/claude-tmux" "$BIN_DIR/claude-tmux"
fill "$SCRIPT_DIR/smartpanel.in" | put "$BIN_DIR/smartpanel"
run chmod +x "$BIN_DIR/smartpanel"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "note: $BIN_DIR is not on your PATH — add it to use 'smartpanel' and 'claude-tmux' directly." ;;
esac

# ---- 5. the settings directory (config.yaml is written by Settings, on first change) ----
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/smartpanel"
run mkdir -p "$CONF_DIR"

# ---- 6. the systemd user service ----
SVC_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
run mkdir -p "$SVC_DIR"
fill "$SCRIPT_DIR/smartpanel.service.in" | put "$SVC_DIR/smartpanel.service"

# ---- 7. the D-Bus session service (on-demand start for --dashboard / --palette / HUD) ----
DBUS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/dbus-1/services"
run mkdir -p "$DBUS_DIR"
fill "$REPO_DIR/data/dev.omdev.SmartPanel.service.in" | put "$DBUS_DIR/dev.omdev.SmartPanel.service"

run systemctl --user daemon-reload
run systemctl --user enable smartpanel.service
if (( START )); then
  run systemctl --user restart smartpanel.service
  run systemctl --user status --no-pager smartpanel.service || true
fi

echo
echo "smartPanel installed. Logs: journalctl --user -fu smartpanel"
echo "  open the dashboard:    smartpanel --dashboard      (or middle-click the bar item)"
echo "  command palette:       smartpanel --palette        (Ctrl+Alt+Space by default)"
echo "  optional desktop HUD:  ./scripts/install-hud.sh"
echo "If no icon appears: GNOME needs the AppIndicator extension (preinstalled on Ubuntu;"
echo "elsewhere install appindicatorsupport@rgcjonas.gmail.com)."
