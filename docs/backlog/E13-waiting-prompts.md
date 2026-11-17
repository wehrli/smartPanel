# E13 · Waiting prompts

**Shows:** the question a waiting session is asking (a permission prompt or an
AskUserQuestion box), its options, and how long it has been waiting. Lets you answer
from the panel.
**Appears in:** Cockpit cards, the Overview "Needs you" card, Board cards ("4m waiting"),
the palette ("Answer · …").
**Already built:** `activity.classify_pane` knows a session is WAITING, but not what it's
asking. `tmux.send_text` / `send_enter` can type into a pane.

Answering sends keys into someone's live session, so the safety tasks (SP-1303, SP-1308)
are part of the feature, not optional.

---

### SP-1301 · BE · Parse the prompt from the pane
Size M
Pure `parse_prompt(pane_text) -> Prompt | None` with `question`, `options: list[str]`,
`cursor` (which option has `❯`) and `kind` (`permission` or `question`). Built on the same
structural markers as `classify_pane` (modal footer and selection cursor), never on phrases.
- [ ] Fixtures: file edit permission, Bash permission, AskUserQuestion with 3 and 4
      options, folder-trust prompt (options without numbers)
- [ ] Returns `None` for a user message that merely contains `1.` and `2.`

### SP-1302 · BE · Prompt fingerprint
Size S · Depends on SP-1301
A stable hash of question and options. The UI passes it back when answering, so an
answer can't land on a different prompt that appeared in the meantime.

### SP-1303 · BE · Answer a prompt safely
Size M · Depends on SP-1302
`answer(session, option_index, fingerprint) -> AnswerResult` (`sent`, `prompt_changed`,
`not_waiting`, `failed`). It re-captures the pane and checks that the state is WAITING and
the fingerprint matches before sending anything. Then it sends the option's number (or
arrow keys and Enter when the options have no numbers), and confirms the prompt is gone
within 2 s.
- [ ] Runs off the main loop if the confirmation wait is used

### SP-1304 · BE · Waiting since
Size S · Depends on SP-1201
Record when a session was first seen WAITING, and clear it when it leaves that state, so
the UI can say "waiting 4 min".

### SP-1305 · FE · Prompt block on Cockpit cards
Size M · Depends on SP-1210, SP-1301
A mono block with the question, then the options with `❯` on the cursor row. Below, one
button per option (`1 · Yes`, `2 · Allow all`, `3 · No`, labels cut at the first comma;
the first is the suggested style) and **Open terminal**. If the prompt couldn't be
parsed, show "This session is waiting for you" and only **Open terminal**.

### SP-1306 · FE · Answer feedback
Size S · Depends on SP-1303, SP-2502
On `sent`: toast `Sent “1” to Fix swap alert hysteresis`, and the card shows running.
On `prompt_changed`: error toast "The prompt changed. Open the terminal to answer.", with
an **Open** action. On `not_waiting`: refresh quietly.

### SP-1307 · FE · Waiting time on Board and Overview
Size S · Depends on SP-1304
Board waiting cards show `4m waiting`; the Overview "Needs you" rows show it after the
question.

### SP-1308 · QA · Never type into the wrong state
Size S · Depends on SP-1303
Tests with a fake runner: an idle pane, a running pane, a changed prompt, and a pane that
vanished all result in zero `send-keys` calls.
