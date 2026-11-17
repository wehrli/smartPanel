#!/usr/bin/env bash
# Remove the smartPanel user service, its D-Bus service file and the `smartpanel` /
# `claude-tmux` launchers. Leaves the source tree, ~/.config/smartpanel (your
# settings), ~/.claude and any running Claude tmux sessions untouched.
# The HUD extension, if installed: gnome-extensions uninstall smartpanel-hud@omdev.dev
set -euo pipefail

SVC="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/smartpanel.service"
systemctl --user disable --now smartpanel.service 2>/dev/null || true
rm -f "$SVC"
systemctl --user daemon-reload

rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/dbus-1/services/dev.omdev.SmartPanel.service"

for name in claude-tmux smartpanel; do
  f="$HOME/.local/bin/$name"
  if [[ -L "$f" ]] || { [[ -f "$f" ]] && grep -q "smartPanel's scripts/install.sh" "$f"; }; then
    rm -f "$f"
  fi
done

echo "smartPanel uninstalled. Settings kept in ${XDG_CONFIG_HOME:-$HOME/.config}/smartpanel/."
