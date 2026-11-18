# ADR 0001: Dashboard toolkit (D1, SP-0001)

Status: accepted.

## Context

v1 was GTK 3 throughout: the top-bar item went through `libayatana-appindicator`
(GTK 3 only) and the dashboard was a GTK 3 window. The v2 design
(`docs/design/prototype.html`) is a libadwaita-style window with a navigation sidebar, a
command palette and toasts. It needs GTK 4 and libadwaita.

PyGObject cannot load GTK 3 and GTK 4 into one process: the first `gi.require_version`
wins and the other import fails.

## Options

1. **GTK 3 window styled with CSS to look like libadwaita.** One process, keeps
   libayatana. It means imitating `AdwNavigationSplitView`, `AdwToastOverlay`,
   `AdwPreferencesGroup` and the style classes by hand, and a dark/light switch with no
   `AdwStyleManager`. The look keeps drifting from the platform.
2. **GTK 4 + libadwaita window in a second process**, with a GTK 3 tray process next to
   it. Native widgets, but every action and state change crosses an IPC boundary. That
   means two processes to start, two to keep alive and one state model serialised
   between them.
3. **GTK 4 + libadwaita in one process, with no libayatana.** The tray protocols are
   plain D-Bus, so they can be spoken directly over `Gio.DBus`:
   StatusNotifierItem (`org.kde.StatusNotifierItem`) for the item and
   `com.canonical.dbusmenu` for its menu. Neither needs a toolkit.

## Decision

Option 3. `smartpanel` is a single `Adw.Application` (`dev.omdev.SmartPanel`).
`smartpanel/tray/sni.py` and `tray/dbusmenu.py` implement the two protocols over Gio. The
Ubuntu AppIndicator extension reads the text label from the SNI property
`XAyatanaLabel`. This was checked in
`/usr/share/gnome-shell/extensions/ubuntu-appindicators@ubuntu.com/appIndicator.js`.

The comparison of start-up time and memory that the spike also asked for was not needed:
the PyGObject constraint rules out option 1 for the window and makes option 2 pay for
IPC with nothing gained.

`gir1.2-adw-1` and `gir1.2-gtk-4.0` are in the Ubuntu archive from 24.04 (libadwaita
1.5). The install line in `README.md` is:

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 python3-cairo \
                 gir1.2-notify-0.7 python3-psutil python3-yaml tmux terminator
```

## Consequences

- There is no IPC between the tray and the window. Both read one `Store`
  (`smartpanel/state/`). Single instance and the CLI verbs (`--palette`, `--dashboard`,
  `--hud`) come from `Gio.Application`.
- We own the SNI and DBusMenu code. Its behaviour against the host is pinned by
  `tests/test_tray_*.py`, and the host's quirks are documented in the `sni.py` docstring.
- Nothing may import GTK 3 again. `tests/test_retirement.py` guards the deleted v1
  modules.
- Hosts other than the Ubuntu AppIndicator extension (KDE, other SNI hosts) get the
  icon and the menu but may ignore `XAyatanaLabel`.
