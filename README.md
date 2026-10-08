# smartPanel

Live system metrics + Claude Code consumption in your GNOME top bar.

Inspired by [turing-smart-screen-python](https://github.com/aluzed/turing-smart-screen-python), but rendered into an Ubuntu panel applet instead of a USB LCD — and with Claude Code token/cost tracking baked in.

```
● Activities  Files  Firefox   🔴🔃92% ⚡78% 🌡62° ⬇12M $1.34 🔋 🔊 22:14
                                    ▲ click to expand
                                   ┌──────────────────────────┐
                                   │ CPU   ████████░░  78%    │
                                   │       62°C  3.2 GHz       │
                                   │ MEM   █████░░░░░  48%    │
                                   │       15.4 / 32 GB        │
                                   │ NET   ↓ 12.4 MB/s         │
                                   │       ↑  0.3 MB/s         │
                                   │ ─────────────────────────│
                                   │ CLAUDE  today: $1.34      │
                                   │   webapp       $0.82      │
                                   │   apiserver    $0.31      │
                                   │   cli-tool     $0.21      │
                                   └──────────────────────────┘
```

## Install

Tested on Ubuntu 24.04 (GNOME, Wayland). Everything comes from apt — no pip, no venv:

```bash
# 1. Dependencies (tmux + terminator are only needed for Claude session management)
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
                 gir1.2-ayatanaappindicator3-0.1 gir1.2-notify-0.7 \
                 python3-psutil python3-yaml tmux terminator

# 2. Get the source somewhere permanent. The service runs from this folder, so don't delete it.
git clone https://github.com/wehrli/smartPanel.git ~/apps/smartPanel
cd ~/apps/smartPanel
#    (or from a release tarball: tar -xzf smartpanel-0.1.0.tar.gz -C ~/apps)

# 3. Install + start (systemd user service, autostarts on login)
./scripts/install.sh
```

`install.sh` checks the dependencies first and prints the exact `apt` line if anything is missing. It links `claude-tmux` into `~/.local/bin`, then installs and starts `~/.config/systemd/user/smartpanel.service`. If you have no `/journal` skill in `~/.claude`, it turns journal-on-close off (see below). Run it again after updating the folder. `./scripts/uninstall.sh` removes the service.

To try it without installing: `python3 -m smartpanel --theme default` from the source folder.

On non-Ubuntu GNOME (Fedora, Arch, vanilla GNOME) install the AppIndicator extension first:
<https://extensions.gnome.org/extension/615/appindicator-support/>

To build the shareable tarball from a checkout: `./scripts/package.sh` → `dist/smartpanel-<version>.tar.gz`.

## Themes

Built-in presets:

- `default` — balanced: CPU, memory, network, Claude summary.
- `claude-focus` — Claude metrics first, system metrics secondary.

Author your own — see `smartpanel/theme/presets/default.yaml` for the format. The theme YAML is a subset of the original turing-smart-screen theme format (so you can drop in an existing theme; pixel-layout fields are ignored) plus a `smartpanel:` block for top-bar templating and Claude options.

```yaml
STATS:
  CPU:
    PERCENTAGE:
      INTERVAL: 2
      TEXT:  { SHOW: True }
      GRAPH: { SHOW: True, MIN_VALUE: 0, MAX_VALUE: 100 }
    TEMPERATURE:
      INTERVAL: 5
      TEXT:  { SHOW: True, SHOW_UNIT: True }
  CLAUDE:
    COST_TODAY:
      INTERVAL: 30
      TEXT:  { SHOW: True }

smartpanel:
  top_bar_format: "{SWAP.STATUS} ⚡{CPU.PERCENTAGE:.0f}% ${CLAUDE.COST_TODAY:.2f}"
  menu_order: [CPU, MEMORY, NET, CLAUDE]
  claude:
    show_per_project: true
    cost_horizon: today
```

## Swap alert

A full swap partition is the point where a Linux desktop stops being busy and
starts thrashing — and it is invisible in the usual readouts, because RAM has
been pinned at 100% for a while by then. smartPanel watches it and says so.

**In the top bar**, the swap gauge is always on, and grows a severity glyph as
soon as swap crosses the warning line:

```
🔃12%      normal                (< 60%)
⚠🔃62%     swap filling up       (≥ 60%)
🔴🔃92%    swap almost full      (≥ 85%)
🔃—        no swap configured
```

It sits at the **front** of the label, because GNOME truncates long panel labels
from the right — a warning on the right is exactly the part that gets eaten by
the `…`. A theme that doesn't place the gauge itself still gets the alert
prefixed onto its label while swap is high.

**As a desktop notification**, once on the way up and once on the way back down:

```
🔴 Swap almost full
   Swap is 92% full (7.4 of 8.0 GiB). The system will start
   thrashing — close something heavy.
```

It does not nag. Entering a level notifies once; a *sustained* critical repeats
every 10 minutes; recovering to warning is silent (only the badge changes) and
returning to normal says so once. Crossings use a 5-point hysteresis band, so
swap hovering on a boundary can't flicker the badge or re-fire the bubble.

**In the dashboard window**, a coloured banner pinned above everything else,
plus a `🔃 Swap` section with the usual percentage / used / total rows.

Tune it with environment variables (all optional):

| Variable | Default | Meaning |
|---|---|---|
| `SMARTPANEL_SWAP_WARN` | `60` | Percent at which the ⚠ badge appears |
| `SMARTPANEL_SWAP_CRIT` | `85` | Percent at which it escalates to 🔴 |
| `SMARTPANEL_SWAP_HYSTERESIS` | `5` | Points below a boundary before stepping back down |
| `SMARTPANEL_SWAP_REPEAT` | `600` | Seconds before a still-critical alert repeats (`0` disables) |

Swap is polled every 10s **regardless of the theme**, so the alert still works
on a stock turing-smart-screen theme that has never heard of a `SWAP` category.
Themes place the gauge with `{SWAP.STATUS}` in `top_bar_format` (what both
shipped presets do), or `{SWAP.ALERT}` for the alert-only variant that stays
empty while swap is fine. Either binding opts out of the automatic prefix, so
the badge can never render twice. A machine with no swap configured reports `—`
and never alerts.

Notifications go through libnotify, falling back to `notify-send`, and finally
to a log line. A missing notification daemon degrades the alert; it never
breaks the panel.

## Claude consumption

smartPanel reads `~/.claude/projects/<encoded-path>/*.jsonl` — the same transcript files Claude Code already writes. No extra setup, no API calls.

It surfaces:

- Tokens today / this week / lifetime (input, output, cache hit ratio).
- Estimated $ cost using current model pricing (Opus / Sonnet / Haiku tracked separately).
- Per-project breakdown — top N projects by spend today.
- **What the compliments cost** — see below.

Pricing is hardcoded in `smartpanel/claude_stats/pricing.py`. Update it when Anthropic publishes new rates — PRs welcome.

## Gratification counter

How much are you paying for "Good question", "You're absolutely right", "Great goal — that's the right thing"? smartPanel scans the assistant's prose for gratification, sizes it in tokens, and prices it — so the "would I save anything by turning this off?" question has a number attached.

```bash
smartpanel --praise-report      # one-shot, no display server needed
```

```
            phrases    tokens    written     echoed      total   of spend
today             7       111    $0.0083      $0.01      $0.02      0.01%
week             11       189      $0.01      $0.01      $0.03      0.00%
lifetime         66      1.0k      $0.08      $0.11      $0.19      0.01%

Most expensive habits (lifetime)
  you're right          24
  good question         18
  good idea             12
```

The same numbers are panel metrics (`CLAUDE.PRAISE_COST_LIFETIME`, `PRAISE_TOKENS_TODAY`, `PRAISE_PHRASES_TODAY`, `PRAISE_SHARE`, and the `PRAISE_SUMMARY` / `TOP_PRAISE` blocks) — the `claude-focus` theme shows them all.

Two costs, because flattery is paid for twice:

- **written** — the output tokens spent saying it, at the model's output rate.
- **echoed** — every later turn in that session re-sends it as prompt, at the cache-read rate. On a long session this is usually the larger half.

What it measures, and what it can't:

- Only assistant **prose** — thinking blocks, tool calls, fenced code and quoted text are excluded.
- The unit is the **sentence**: a short sentence containing a compliment is counted whole, since that's what deleting it would remove. A long sentence that merely opens with one is charged only for its opening clause.
- Token counts are **estimated** at ~4 chars/token — transcripts record `output_tokens` per message, never per sentence. Treat the totals as an order of magnitude, not a bill.

Detection lives in `smartpanel/claude_stats/flattery.py`; the phrase table at the top of that file is meant to be edited.

## Manage Claude sessions

smartPanel can also see and control your running Claude Code sessions from the panel — provided you launch them inside tmux with the bundled launcher:

```bash
# Launch Claude in a managed tmux session named "claude-<cwd-basename>"
./scripts/claude-tmux                 # or put it on your PATH
./scripts/claude-tmux -n review       # name it "claude-review"
./scripts/claude-tmux --model opus    # extra args are forwarded to `claude`
```

The dashboard's **🧩 Sessions** section then lists each live session with:

- the **activity glyph** (below) and Claude's own conversation title,
- **click the row** to bring that session's terminal to the front,
- a per-session **✕** to close just that one, **⟳** to re-read its name,
- **✕ Close all (N)** to close every managed session at once.

The top bar shows the live count (`{SESSIONS.STATUS}` — built into both the `default` and `claude-focus` presets; add it to any theme). Only sessions whose tmux name starts with `claude-` are ever listed or killed, so your other tmux work is never touched.

### What each session is doing

Every row leads with a glyph saying what that session is up to right now, and the top-bar badge tallies them — `🧩4 ❓1 ▶2`, collapsing to `🧩4` when nothing is happening:

| Glyph | State | Meaning |
|---|---|---|
| ❓ | `waiting` | Blocked on **you** — a permission prompt, a plan approval, a question. Nothing moves until you answer. |
| ▶ | `running` | Working: generating, running a tool, or waiting on a background agent. |
| · | `idle` | Nothing happening; the input box is free. |

This is read from the session's tmux pane, not its transcript — a permission prompt is a TUI state that never gets written to the transcript, so the pane is the only place it exists. Detection is structural (the modal's own footer and the shape of its choice list), so ordinary prose that happens to ask a question doesn't trip it.

Three metrics are exposed for themes: `{SESSIONS.STATUS}` (the ready-made badge), `{SESSIONS.WAITING}` and `{SESSIONS.RUNNING}` (counts), alongside the existing `{SESSIONS.COUNT}` and `{SESSIONS.NAMES}`.

### Clicking a session

Clicking a row puts you in front of that session's terminal. How that happens depends on your desktop, because **GNOME Wayland does not let one application raise another's window** — `org.gnome.Shell.Eval` is disabled outside unsafe mode and `Shell.Introspect.GetWindows` answers `AccessDenied`, so there is no supported way in. smartPanel tries, in order:

1. `SMARTPANEL_FOCUS_CMD` — your own command, with `{session}` and `{title}` placeholders.
2. The [**Window Calls**](https://extensions.gnome.org/extension/4724/window-calls/) GNOME extension (`window-calls@domandoman.xyz`), which re-exposes window activation on D-Bus. **Install it if you want the real window raised in place** — it is the only route that works on stock GNOME Wayland.
3. `wmctrl` / `xdotool`, for X11 and XWayland sessions.
4. **Re-attach** — open a fresh terminal running `tmux attach -d` on that session. The old client is evicted and its window closes, so you end up with exactly one window per session. tmux owns the screen and the scrollback, so nothing is lost.

Step 4 is the fallback that makes clicking work out of the box on Ubuntu. It replaces a window instead of raising one; set `SMARTPANEL_FOCUS_REATTACH=0` to turn it off if steps 1-3 already work for you.

| Env var | Default | What it does |
|---|---|---|
| `SMARTPANEL_FOCUS_CMD` | *(unset)* | Command tried first, e.g. `wmctrl -a "{title}"`. |
| `SMARTPANEL_FOCUS_REATTACH` | `1` | Allow the re-attach fallback. `0` disables it. |

### Dashboard window

The dropdown menu can overflow a small screen. For the full picture, open the **📊 Dashboard** — a scrollable window that pops up centered, showing every metric plus the live session list. Open it from the top menu item, or **middle-click** the tray icon. Closing it just hides it, so it reopens instantly.

### Journal on close

Closing a session from the dashboard offers to journal it — a Yes/No dialog that auto-confirms after 5s. Sessions younger than 2 minutes (`SMARTPANEL_JOURNAL_MIN_AGE`) close straight away without asking.

On Yes, smartPanel **types `/journal` into the session that's still running** and closes it only once the skill answers. The row shows `⏳ … journaling…` while it works.

Running it in the live session — rather than killing first and resuming the transcript — means the skill's fork inherits the *actual conversation*, so it can record **why** things were decided, not just what changed. No transcript sweep can reconstruct that. The skill decides where the entry goes and returns one confirmation line.

smartPanel also drops a per-session receipt in `~/.claude/smartpanel/journals/<ts>-<project>.md` with that line plus a deterministic **Activity** section (duration, files touched, commands, tool counts) — so if the skill fails, you still get a record. And the session is always closed, even if journaling fails outright.

If the session has already exited (you closed it in its own terminal), smartPanel falls back to resuming it headlessly:

```
claude -p --resume <session-id> --permission-mode acceptEdits --allowedTools …
```

Because the live route inherits the session's permission mode, `scripts/claude-tmux` starts sessions with **`--permission-mode acceptEdits`**. Without it the `/journal` skill's `git` sweep and file write would sit on a permission prompt nobody is watching until the timeout, and the session would close with no entry written. Pass your own `--permission-mode` (or `--dangerously-skip-permissions`) to override it, or set `SMARTPANEL_CLAUDE_PERMISSION_MODE=` empty to launch sessions with no mode flag at all — journaling then only works if you leave the session in accept-edits mode yourself (`shift+tab`).

| Env var | Default | What it does |
|---|---|---|
| `SMARTPANEL_JOURNAL_SKILL` | `/journal` | Slash command to run. The project name is appended as its argument. Empty = journal-on-close off (what `install.sh` sets when you have no `/journal` skill). |
| `SMARTPANEL_JOURNAL_TOOLS` | `Bash Read Write Edit Glob Grep Task TodoWrite` | Tool allowlist for the headless fallback. |
| `SMARTPANEL_JOURNAL_MODEL` | *(session's own)* | Override the model for the headless fallback. |
| `SMARTPANEL_JOURNAL_TIMEOUT` | `300` | Seconds before the run is abandoned. |
| `SMARTPANEL_JOURNAL_IDLE_WAIT` | `120` | Seconds to wait for a busy session to finish its turn before giving up on the live route. |
| `SMARTPANEL_JOURNAL_MIN_AGE` | `120` | Sessions shorter than this are never journaled. |
| `SMARTPANEL_CLAUDE_PERMISSION_MODE` | `acceptEdits` | Permission mode new sessions launch with. Empty = pass no flag. |

Generation runs on a daemon thread, off the GLib loop. Skill runs are serialized — "Close all" journals sessions one at a time so they don't clobber a shared log file, and each session stays up until its own entry is written.

> Requires `tmux` (`sudo apt install tmux`).

## License

MIT.
