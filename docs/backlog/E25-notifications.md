# E25 · Notifications & toasts

**Shows:** desktop notifications for things that happen while you aren't looking (swap,
journal results, budget), and in-window toasts as feedback for your own actions.
**Already built:** `notify.py` (libnotify, `notify-send`, log; one reused handle per key).

---

### SP-2501 · BE · Notification kinds and actions
Size M
Kinds: `swap_warning`, `swap_critical`, `swap_ok`, `journal_ok`, `journal_failed`,
`budget_80`, `budget_100`. Each has its own replacement key and urgency (critical swap is
`critical`). Where libnotify supports actions, add **Open System** / **View**, which call
`OpenDashboard` with the right page.

### SP-2502 · FE · Toast component
Size S · Depends on SP-2104
A pill at the bottom of the window: text, an optional action button, an error style, and
auto-dismiss after 4 s (6 s for errors). A new toast replaces the current one.

### SP-2503 · BE · Respect Do Not Disturb
Size S · Depends on SP-2501
When `org.gnome.desktop.notifications show-banners` is false, only critical swap
notifications are sent; the rest go to the log.
