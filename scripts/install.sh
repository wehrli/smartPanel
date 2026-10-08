#!/usr/bin/env bash
# Install smartPanel as a systemd --user service that autostarts on login.
#
# Runs straight from this source tree with the system python3 — no pip, no
# venv. On Ubuntu 24.04+ system-wide pip is blocked (PEP 668), and PyGObject
# has to come from apt anyway, so every dependency is an apt package.
#
#   ./scripts/install.sh            # check deps, install + start the service
#   SMARTPANEL_PYTHON=/path/python ./scripts/install.sh   # use another interpreter
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_DIR="$( dirname "$SCRIPT_DIR" )"
PYTHON_BIN="${SMARTPANEL_PYTHON:-$(command -v python3)}"

APT_PKGS="python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 gir1.2-notify-0.7 python3-psutil python3-yaml tmux terminator"

# ---- 1. hard requirements: the panel cannot start without these ----
missing=()
check_py() { "$PYTHON_BIN" -c "$2" >/dev/null 2>&1 || missing+=("$1"); }
check_py "python >= 3.11"         'import sys; assert sys.version_info >= (3, 11)'
check_py "PyGObject + GTK 3"      'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk'
check_py "AyatanaAppIndicator3"   'import gi; gi.require_version("AyatanaAppIndicator3", "0.1"); from gi.repository import AyatanaAppIndicator3'
check_py "psutil"                 'import psutil'
check_py "PyYAML"                 'import yaml'

if (( ${#missing[@]} )); then
  echo "smartPanel: missing for $PYTHON_BIN:" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  echo >&2
  echo "Install them with:" >&2
  echo "  sudo apt install $APT_PKGS" >&2
  exit 1
fi

# ---- 2. soft requirements: only the session-management features need these ----
for tool in tmux terminator claude; do
  command -v "$tool" >/dev/null 2>&1 \
    || echo "warning: '$tool' not found — Claude session management won't work until it is installed."
done

# ---- 3. claude-tmux on PATH, so sessions can be started from any shell ----
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
ln -sf "$SCRIPT_DIR/claude-tmux" "$BIN_DIR/claude-tmux"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "note: $BIN_DIR is not on your PATH — add it to use 'claude-tmux' directly." ;;
esac

# ---- 4. journal-on-close needs a /journal skill; switch it off if there is none ----
journal_env=""
if [[ ! -e "$HOME/.claude/skills/journal/SKILL.md" && ! -e "$HOME/.claude/commands/journal.md" ]]; then
  journal_env='Environment=SMARTPANEL_JOURNAL_SKILL='
  echo "note: no /journal skill in ~/.claude — journal-on-close is disabled (closing a session just closes it)."
fi

# ---- 5. the service ----
SVC_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$SVC_DIR"
sed \
  -e "s|@PYTHON@|${PYTHON_BIN}|g" \
  -e "s|@REPO@|${REPO_DIR}|g" \
  -e "s|@JOURNAL_ENV@|${journal_env}|g" \
  "$SCRIPT_DIR/smartpanel.service.in" > "$SVC_DIR/smartpanel.service"

systemctl --user daemon-reload
systemctl --user enable smartpanel.service
systemctl --user restart smartpanel.service
systemctl --user status --no-pager smartpanel.service || true

echo
echo "smartPanel installed. Logs: journalctl --user -fu smartpanel"
echo "If no icon appears: GNOME needs the AppIndicator extension (preinstalled on Ubuntu)."
