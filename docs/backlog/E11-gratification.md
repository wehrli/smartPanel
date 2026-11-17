# E11 · Gratification on receipts

**Shows:** what the compliments ("Good question", "You're absolutely right") cost on a
given day: emitting them, and their echo in later turns.
**Appears in:** receipts (the "tip line").
**Already built:** `flattery.py`, per-bucket `praise_cost_usd` and `praise_echo_cost_usd`.

---

### SP-1101 · BE · Praise cost per day
Size S · Depends on SP-1001
Each `DayBucket` carries `praise_cost_usd` and `praise_echo_cost_usd` (already on
`Bucket`, so this is mostly exposing them in the state's day list).

### SP-1102 · FE · Compliment lines on the receipt
Size S · Depends on SP-1004
`incl. compliments 0.07` in the accent red, and `echo of them later 0.21` underneath.
Hidden on days with none.

### SP-1103 · FE · Say it's an estimate
Size S · Depends on SP-1102
Tooltip or footnote: "Estimated from the text length. Transcripts record tokens per
message, not per sentence." (The CLAUDE.md rule: the UI must not imply this is measured.)
