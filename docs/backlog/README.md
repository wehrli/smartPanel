# smartPanel backlog: "the mix" UI

The design is the clickable prototype in [`docs/design/prototype.html`](../design/prototype.html)
(also published at https://claude.ai/artifact/GU3YbYiCEXFy9bWnVTCCwe). It combines the
styles picked in the UI study:

| Surface | Styles |
|---|---|
| Top bar | **A1** numbers · **A4** live graphs · **A5** ticker · **A6** session strip, each switchable |
| Dashboard window | **B1** Adwaita shell holding **B2** Cockpit and **B3** Board (Sessions), **B7** Receipts (Spend) |
| Overlays | **B5** command palette · **B6** desktop HUD |

## How this backlog is cut

**An epic is one piece of information the user sees**, such as swap pressure, today's
spend or the session list. Each epic lists everything needed to show it everywhere it
appears: top bar, menu, window pages, HUD and palette. A few epics are surfaces or
platform (E00, E19 to E27) because they're shared by every piece of information.

**Each task is tagged with a layer:**

- **BE** (back end): sensors, aggregator, session control, state model, D-Bus, config,
  persistence. GTK-free, unit-testable, no display needed.
- **FE** (front end): anything drawn. That means the AppIndicator label/icon and menu, the
  dashboard window, the HUD, the palette, dialogs, toasts and notifications.
- **SPIKE**: a time-boxed investigation that ends in a written decision.
- **QA**: tests, fixtures, screenshots.

Task IDs are `SP-EENN`, where `EE` is the epic number. Sizes: **S** is under half a day,
**M** is 1 to 2 days, **L** is 3 days or more. "Depends on" lists hard blockers only.

## Decisions taken (2026-11-17)

See `docs/architecture/v2.md` for the details.

- **D1: fully GTK4.** We drop libayatana-appindicator and speak StatusNotifierItem and
  DBusMenu ourselves over Gio, so the whole app is one GTK4/libadwaita process.
- **D2: coloured bar graphics without an extension.** The Ubuntu AppIndicator extension
  keeps a wide icon's aspect ratio when the icon is a PNG file path ≥ 1.5× wider than tall,
  so the strip and graphs are drawn with cairo into such a file.
- **D3: the HUD is a separate GNOME Shell extension** (`smartpanel-hud@omdev.dev`) reading
  our D-Bus API.

## Decisions that block everything else (original questions)

These are SPIKE tasks in [E00](E00-foundation.md). Do them first, since the answers change
how most FE tasks are built.

1. **D1: dashboard toolkit (SP-0001).** B1 is a libadwaita design. PyGObject can't load
   GTK 3 and GTK 4 in the same process, and AyatanaAppIndicator3 is GTK 3. Options:
   (a) keep one GTK3 process and style the window to look like Adwaita with CSS, or
   (b) run the dashboard as its own GTK4/libadwaita process talking to the indicator
   daemon over D-Bus. The rest of the backlog assumes **(b)**, with a D-Bus state service
   (SP-0004, SP-0005). If (a) wins, SP-0005 shrinks to in-process signals.
2. **D2: graphics in the top bar (SP-0002).** An AppIndicator label is plain text and gets
   one icon. The coloured A6 strip and the A4 graphs must be drawn into that icon with cairo
   (it's unknown whether the Ubuntu AppIndicator extension keeps a wide icon's aspect
   ratio), or fall back to uncoloured Unicode (`▁▃▅▇`, `▮▯`). A GNOME Shell extension is
   the only route to the prototype's exact look.
3. **D3: HUD on Wayland (SP-0003).** A Wayland client can't place itself, stay below
   other windows or skip the taskbar on its own. B6 needs a Shell extension, a layer-shell
   workaround, or acceptance that it's an ordinary window the user positions.
4. **D4: palette shortcut.** An app can't grab a global key on Wayland. The palette is
   `smartpanel --palette`, bound by the user (or by us, with consent) as a GNOME custom
   shortcut (SP-2204).

## Epics

| # | Epic | Information it shows | BE | FE | Other |
|---|---|---|---|---|---|
| [E00](E00-foundation.md) | Foundation | Shared state, D-Bus, history, config, design tokens, widget kit, demo mode | 5 | 3 | 5 |
| [E01](E01-swap.md) | Swap pressure | Swap %, GB used, warn/critical level | 3 | 8 | 1 |
| [E02](E02-cpu.md) | CPU | CPU %, 15-min history, temperature | 2 | 4 | 0 |
| [E03](E03-memory.md) | Memory | RAM %, GB used, history | 1 | 4 | 0 |
| [E04](E04-disk.md) | Disk | Disk usage of the root mount | 1 | 2 | 0 |
| [E05](E05-network.md) | Network | Down/up rate, interface, history | 2 | 2 | 0 |
| [E06](E06-gpu.md) | GPU | GPU %, VRAM, temp, or "no sensor" | 1 | 2 | 0 |
| [E08](E08-spend-today.md) | Claude spend today | $ today, tokens, burn rate, cache hits, by project | 4 | 6 | 1 |
| [E09](E09-budget.md) | Daily budget | Budget, % used, ETA, over-budget | 2 | 3 | 0 |
| [E10](E10-spend-history.md) | Spend history & receipts | Per-day spend by project and model | 2 | 4 | 1 |
| [E11](E11-gratification.md) | Gratification | Cost of compliments on receipts | 1 | 2 | 0 |
| [E12](E12-sessions.md) | Session list & activity | Sessions, state, what each is doing | 4 | 10 | 1 |
| [E13](E13-waiting-prompts.md) | Waiting prompts | The question a session asks, and its options | 4 | 3 | 1 |
| [E14](E14-session-identity.md) | Session titles & cost | Title, project, attached, per-session $ | 2 | 3 | 0 |
| [E15](E15-new-session.md) | New session | Folder choice, launch progress, result | 2 | 4 | 0 |
| [E16](E16-close-and-journal.md) | Close & journal | Journal confirm, progress steps, result | 3 | 6 | 1 |
| [E17](E17-focus.md) | Focus | Which terminal came forward, and how | 1 | 3 | 0 |
| [E18](E18-journal-history.md) | Journal history | Past journal receipts | 2 | 3 | 0 |
| [E19](E19-top-bar.md) | Top bar composition | The A1/A4/A5/A6 label | 4 | 4 | 0 |
| [E20](E20-bar-menu.md) | Bar menu | The click menu | 0 | 3 | 0 |
| [E21](E21-dashboard-shell.md) | Dashboard shell | Window, navigation, badges | 0 | 9 | 0 |
| [E22](E22-command-palette.md) | Command palette | Actions and sessions by keyboard | 2 | 3 | 0 |
| [E23](E23-desktop-hud.md) | Desktop HUD | Spend and sessions on the wallpaper | 1 | 3 | 0 |
| [E24](E24-settings.md) | Settings | Every setting in the prototype | 2 | 2 | 0 |
| [E25](E25-notifications.md) | Notifications & toasts | Desktop bubbles and in-window toasts | 2 | 1 | 0 |
| [E26](E26-app-states.md) | App states | Cold start, first run, tmux missing, no sessions, no swap | 3 | 5 | 0 |
| [E27](E27-packaging-qa.md) | Packaging & QA | Screenshots, docs, install, performance | 0 | 0 | 4 |
| | **Total** | **173 tasks** | **56** | **102** | **15** |

E07 is skipped on purpose. Battery has a sensor but isn't in the prototype; it keeps its
current menu row.

## Suggested order

1. **Decide:** SP-0001, SP-0002, SP-0003.
2. **Platform BE:** SP-0004 to SP-0007, SP-0011, SP-0012 (demo mode makes every later FE task
   testable without real sessions).
3. **Information BE, in parallel per epic:** E01 to E18 BE tasks. Each one adds its fields to
   the state model and has unit tests.
4. **Platform FE:** SP-0008 to SP-0010, then E21 (shell), E19 and E20 (bar and menu).
5. **Information FE, in parallel per epic:** pages, cards, rows.
6. **Overlays:** E22 (palette), E23 (HUD).
7. **States and polish:** E24 to E27.

## Conventions

- Follow `CLAUDE.md`. The main-loop budget stays under 50 ms, and anything slower moves to a
  worker thread that reports back with `GLib.idle_add`. Sensors never raise. Parser and
  aggregator tests use real JSONL fixtures, not mocks.
- Every new pure function (parsers, formatters, policies, matchers) gets unit tests that
  don't need a display server.
- Every FE task is done when it renders correctly in **each demo scenario** (SP-0012):
  busy, calm, alert, empty, many, cold, first, notmux, noswap.
