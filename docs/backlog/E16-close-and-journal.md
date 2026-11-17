# E16 · Close & journal

**Shows:** the choice to journal before closing, each step of the journal run, and the
result (journaled, or closed with a receipt only).
**Appears in:** confirm dialog, journaling rows and Cockpit steps, the Board drop zone,
close-all dialog, notifications and toasts.
**Already built:** `journal.journal_in_session`, `write_journal_async`, `_SKILL_LOCK`,
the 5-second auto-confirm dialog, `JOURNAL_MIN_AGE_S`, receipts.

---

### SP-1601 · BE · Journal progress events
Size M · Depends on SP-1203
`write_journal` reports its step through a callback, bounced to the main loop:
1. wait for the session to go idle, 2. type `/journal <project>`, 3. skill writing to the
monthly log, 4. close the tmux session, 5. write the receipt.
The current step goes into the session's `journaling` phase in the state.
- [ ] Headless-resume fallback reports the same steps (2 becomes "resume headlessly")

### SP-1602 · BE · Journal outcome
Size S · Depends on SP-1601
The run ends with `JournalOutcome` (`journaled`, `skill_failed`, `timeout`) plus the
receipt path and the skill's confirmation line. The session is closed in every case (the
existing `_once` and `finally` guarantee).

### SP-1603 · BE · Close-all queue
Size S · Depends on SP-1601
Close all runs sequentially (the skill lock) and reports `n of m`. Sessions younger than
the minimum age are closed immediately without journaling.

### SP-1604 · FE · Journal confirm dialog
Size S · Depends on SP-2104
"Journal “Fix swap alert hysteresis” before closing?" with the text "It has been open 1h02.
smartPanel types /journal smartPanel into the session, waits for the entry, then closes
it." Buttons: **Close without** and **Journal & close (5)**, counting down and confirming
on its own (setting `sessions.confirm_seconds`). Esc cancels.

### SP-1605 · FE · Journaling progress
Size M · Depends on SP-1601
Cockpit card: the five steps as a checklist (done ✓, current with spinner, upcoming ○,
failed ✕ "failed, closing anyway"). Overview row: the current step's text. Board: the
session moves into the drop zone with its checklist. Strip block turns purple.

### SP-1606 · FE · Close-all dialog
Size S · Depends on SP-2104
"Close all 4 sessions?" with "3 of them get a journal entry first, one at a time, because
they share one monthly log. Running work is interrupted." Buttons: **Cancel**, **Close all**
(destructive style).

### SP-1607 · FE · Drag to journal & close
Size M · Depends on SP-1211
Dragging a Board card onto the drop zone runs the same flow as ✕ (dialog included).
Starting and journaling cards can't be dragged.

### SP-1608 · FE · Result feedback
Size S · Depends on SP-1602, SP-2501, SP-2502
Journaled: notification "Journal written · <title>" and toast "Journaled & closed" with a
**View** action that opens Journal with that entry selected. Failed: notification "Journal
failed · <title>", error toast "Closed. The journal failed, receipt saved." with **View**.

### SP-1609 · FE · Short sessions and journaling off
Size S
Under the minimum age, or with journaling off in Settings: close at once, with a toast
that says why ("Open under 2 min, so no journal.").

### SP-1610 · QA · Journal flow in demo mode
Size S · Depends on SP-0012
Demo mode simulates the five steps and a failure switch, so the FE tasks above can be
checked without running the real skill.
