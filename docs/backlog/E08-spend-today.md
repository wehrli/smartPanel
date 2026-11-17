# E08 · Claude spend today

**Shows:** dollars spent today, tokens, burn rate ($/h over the last hour), cache-hit
ratio, week and lifetime totals, today's split by project, hourly spend.
**Appears in:** A1 numbers and A5 ticker in the top bar, bar menu, Overview "Today" card
and project bars, Spend page header, HUD headline, palette status line.
**Already built:** `Aggregator.refresh()` → `Snapshot` (today/week/lifetime buckets,
`per_project_today`, `per_model_today`, `burn_rate_per_hour`), `sensors/claude.py`.

---

### SP-0801 · BE · Aggregator off the main loop
Size M · Depends on SP-0004
The first scan reads every transcript and can stall the GLib loop (the CLAUDE.md gotcha).
Run `refresh()` on a worker thread; push the snapshot into the state with `GLib.idle_add`.
Only one refresh in flight at a time; a tick that finds one running is skipped.
- [ ] Main loop never blocks more than 50 ms during a cold scan of 1,000+ files
- [ ] Reports progress (`files_done`, `files_total`) for E26's cold-start state

### SP-0802 · BE · Local-time day boundaries
Size S
`_today_start` uses `now` in UTC, so "today" resets at 01:00 or 02:00 in Paris, not
midnight. Use the local timezone for today, the week and the per-day buckets (E10).
- [ ] Fixture test with events at 23:30 and 00:30 local time

### SP-0803 · BE · Hourly spend for today
Size S · Depends on SP-0802
24 hourly cost buckets for today in the snapshot (the Overview card's bar chart).

### SP-0804 · BE · Spend section in the panel state
Size S · Depends on SP-0004
`cost_today`, `cost_week`, `cost_lifetime`, `tokens_today` (raw and human, e.g. `41.2M`),
`burn_per_hour`, `cache_hit_pct`, `per_project_today` (sorted by cost),
`hourly_today`, `updated_at`.

### SP-0805 · FE · Spend in the top bar
Size S · Depends on SP-1901
A1 last segment: `23.84` (no `$`, to save width). A5 ticker item: `$23.84 today`.
First run shows `—`.

### SP-0806 · FE · Overview "Today" card
Size S · Depends on SP-0009, SP-0803
`$23.84`, hourly bars (current hour highlighted), sub-line `41.2M tokens · $3.10/h`.

### SP-0807 · FE · Today by project
Size M · Depends on SP-0009
Overview card with one bar per project (name, bar, amount), largest first, max 6 rows plus
"others". Colours stay stable per project (hash of the name into a fixed palette) so a
project keeps its colour across pages and days. An **Open receipts** button goes to Spend.

### SP-0808 · FE · Spend in the bar menu
Size S · Depends on SP-2001
Section "Claude · today" with rows `Spent $23.84` and `Burn rate $3.10/h`.

### SP-0809 · FE · Spend on the HUD
Size S · Depends on SP-2302
Big `23.84`, then `usd · 41.2M tokens · 3.10/h`.

### SP-0810 · FE · Spend page header
Size S · Depends on SP-1003
Four cards: Today, This week, Lifetime, Cache hits.

### SP-0811 · QA · Spend figures match `--praise-report`
Size S
Test that today/week/lifetime in the state match the existing report's numbers for the
same fixtures, so the UI and the CLI never disagree.
