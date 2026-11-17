# E09 · Daily budget

**Shows:** the daily budget, how much of it is spent, when it will run out at the current
burn rate, and by how much it's exceeded.
**Appears in:** Overview budget card, receipts, Settings, an optional notification.
**Already built:** nothing.

---

### SP-0901 · BE · Budget setting and forecast
Size S · Depends on SP-0007, SP-0804
Setting `budget.daily_usd` (default off). Pure function
`forecast(spent, budget, burn_per_hour, now) -> Forecast(pct, eta | None, over_by)`,
where `eta` is a local time today, or `None` with a reason ("never" at zero burn,
"tomorrow" past midnight).
- [ ] Unit tests: zero burn, already over, crossing midnight

### SP-0902 · BE · Budget notifications
Size S · Depends on SP-0901, SP-2501
One notification at 80% and one at 100%, at most once each per local day. Off when no
budget is set.

### SP-0903 · FE · Overview budget card
Size S · Depends on SP-0009
"Daily budget $23.84 / $40.00", progress bar, then "At $3.10/h you reach it at 19:45".
Over budget: red bar and "Over budget by $6.50". Hidden when no budget is set, with a
"Set a daily budget" link to Settings instead.

### SP-0904 · FE · Budget lines on receipts
Size S · Depends on SP-1004
`daily budget 40.00` and `left 16.16` (or `over by 6.50`) on every receipt.

### SP-0905 · FE · Budget in Settings
Size S · Depends on SP-2401
Number field "Daily budget", empty means off.
