# E10 · Spend history & receipts

**Shows:** spend per day for the last 14 days (more on scroll), each day broken down by
project and by model, with token counts.
**Appears in:** the Spend page (B7): a day list on the left, a till-slip receipt on the right.
**Already built:** the aggregator keeps every event, but only sums today/week/lifetime.

---

### SP-1001 · BE · Per-day buckets
Size M · Depends on SP-0802
Extend `Snapshot` with `per_day: dict[date, DayBucket]`, where `DayBucket` holds a total
`Bucket`, `per_project` and `per_model`. Days are local-time days. Keep 90 days in the
state; the window asks for older days on demand.
- [ ] Message-id dedupe still applies across files (first file wins)
- [ ] Fixture tests across midnight and across a resumed session

### SP-1002 · BE · Day list in the panel state
Size S · Depends on SP-1001, SP-0004
`spend_days`: the last 14 days, newest first, each with label (`Today`, `Mon 16 Nov`),
total, per-project, per-model, tokens. Days with no usage are included with zero.

### SP-1003 · FE · Spend page and day list
Size M · Depends on SP-2101, SP-0009
Left column: 14 day rows (label, total, a thin bar scaled to the biggest day); "Today" is
selected on open. Right: the receipt for the selected day. Keyboard ↑↓ moves the selection.

### SP-1004 · FE · Receipt widget
Size M · Depends on SP-0008
Paper look in mono type with a scalloped bottom edge: header `CLAUDE CODE`, date and time,
project lines, model lines (Opus, Sonnet, Haiku), `TOTAL USD`, compliments lines (E11),
budget lines (E09), `41.2M TOKENS · THANK YOU`.
- [ ] Amounts right-aligned with tabular figures

### SP-1005 · FE · Empty day
Size S · Depends on SP-1004
A receipt with "NO USAGE THIS DAY" for a zero day (weekends, holidays).

### SP-1006 · FE · Loading and first-run states on Spend
Size S · Depends on SP-2601, SP-2603
Cold start: spinner, "Reading your transcripts · 812 of 1,284 session files", progress bar.
First run: empty state "No Claude Code usage yet" with a **New session** button.

### SP-1007 · QA · Sums are consistent
Size S · Depends on SP-1001
The 7 per-day totals add up to the week total, and all days add up to lifetime, on the
shared fixtures.
