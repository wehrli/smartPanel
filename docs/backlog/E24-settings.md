# E24 · Settings

**Shows:** every setting from the prototype, grouped: Top bar, Swap alert, Spending,
Sessions, Desktop, Appearance.
**Appears in:** the Settings page.

---

### SP-2401 · FE · Settings page
Size M · Depends on SP-2101, SP-0007
Preference groups:
- **Top bar**: live preview (SP-1908); switches for Session strip (A6), Live graphs (A4),
  Numbers (A1), Rotate readings (A5).
- **Swap alert**: Warning at (slider, %), Critical at (slider, %).
- **Spending**: Daily budget.
- **Sessions**: Journal before closing (switch), Only if open longer than (min),
  Auto-confirm after (s), Re-attach if focus fails (switch), New sessions start in (folder).
- **Desktop**: Desktop HUD (switch), Command palette shortcut (shown, with **Add shortcut**).
- **Appearance**: Window style Follow system / Light / Dark.

### SP-2402 · BE · Settings map and env overrides
Size S · Depends on SP-0007
One table mapping each control to its config key and env var
(`SMARTPANEL_SWAP_WARN`, `SMARTPANEL_JOURNAL_MIN_AGE`, `SMARTPANEL_SESSIONS_DIR`,
`SMARTPANEL_FOCUS_REATTACH`…). A control whose env var is set is greyed out with "Set by
SMARTPANEL_SWAP_WARN".

### SP-2403 · BE · Apply without restart
Size S · Depends on SP-2402
Every setting takes effect immediately through config observers (thresholds, journal ages,
bar composition, HUD, theme).

### SP-2404 · FE · Validation
Size S · Depends on SP-2401
Critical can't go below warning (it's pushed up), numbers can't go negative, a folder that
doesn't exist shows an inline error and isn't saved.
