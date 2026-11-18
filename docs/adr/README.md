# Architecture decision records

Short records of the three v2 spikes (backlog `E00-foundation.md`, SP-0001 to SP-0003).
`docs/architecture/v2.md` summarises them as D1 to D3. These files hold the context,
the options that were weighed and the consequences.

| ADR | Decision |
|---|---|
| [0001](0001-dashboard-toolkit.md) | GTK4 + libadwaita in one process; tray protocols spoken over Gio |
| [0002](0002-top-bar-graphics.md) | Bar graphics as a wide PNG named by absolute path; Unicode text as the fallback |
| [0003](0003-desktop-hud.md) | The desktop HUD is a separate GNOME Shell extension reading the D-Bus API |
