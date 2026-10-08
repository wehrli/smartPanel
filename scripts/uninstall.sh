#!/usr/bin/env bash
# Remove the smartPanel user service and the claude-tmux link. Leaves the source
# tree, ~/.claude and any running Claude tmux sessions untouched.
set -euo pipefail

SVC="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/smartpanel.service"
systemctl --user disable --now smartpanel.service 2>/dev/null || true
rm -f "$SVC"
systemctl --user daemon-reload

LINK="$HOME/.local/bin/claude-tmux"
[[ -L "$LINK" ]] && rm -f "$LINK"

echo "smartPanel uninstalled."
