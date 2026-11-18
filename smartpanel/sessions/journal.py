"""Generate a journal log for a Claude session when it is closed.

A session's transcript lives at `~/.claude/projects/<dash-encoded-cwd>/*.jsonl`,
one file per session, named `<session-id>.jsonl`. On close we do two things:

1. **The workspace journal.** We resume the real session headlessly
   (`claude -p --resume <session-id>`) and run the user's own **`/journal`
   skill** in it. Because the skill inherits the actual conversation, its
   *Decided* section can record reasoning that no transcript sweep could
   reconstruct. The skill owns where it writes (typically an append-only
   log file) and returns a single confirmation line.
2. **A per-session receipt.** We condense the transcript (user/assistant text +
   tool names, dropping bulky tool output) and write a deterministic
   **Activity** section — duration, files touched, commands, tool counts — to
   `~/.claude/smartpanel/journals/`, along with the skill's confirmation line
   (or why it was skipped). No prose summary is generated here; the narrative
   is the skill's job.

Both steps are slow: `run_journal` blocks for minutes, so the controller
(`services/controller.py`) runs it on a worker thread off the GLib main loop.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from smartpanel.sessions import tmux

log = logging.getLogger(__name__)

JOURNAL_DIR = Path.home() / ".claude" / "smartpanel" / "journals"

# The slash command to run in the resumed session. Any skill with a compatible
# contract (takes an optional project scope, returns one confirmation line) can
# be swapped in here. Set it empty to turn journal-on-close off entirely — the
# installer does that for users who have no `/journal` skill.
SKILL_COMMAND = os.environ.get("SMARTPANEL_JOURNAL_SKILL", "/journal").strip()
# Empty means "whatever model the resumed session was using".
SKILL_MODEL = os.environ.get("SMARTPANEL_JOURNAL_MODEL", "")
# The skill sweeps git/gh and writes one file; it runs unattended, so it needs
# an explicit allowlist or every tool call would be denied.
SKILL_TOOLS = (os.environ.get("SMARTPANEL_JOURNAL_TOOLS")
               or "Bash Read Write Edit Glob Grep Task TodoWrite").split()
# A git/gh sweep plus the write takes far longer than a one-shot summary did.
CLAUDE_TIMEOUT_S = int(os.environ.get("SMARTPANEL_JOURNAL_TIMEOUT") or 300)

_MAX_CONDENSED_CHARS = 12000

_FILE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "Update"}


@dataclass
class Digest:
    user_prompts: list[str] = field(default_factory=list)
    assistant_texts: list[str] = field(default_factory=list)
    tool_counts: dict[str, int] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    condensed: str = ""


# ---- transcript discovery ----

def project_dir_for_cwd(cwd: str | os.PathLike) -> Path:
    encoded = str(Path(cwd)).replace(os.sep, "-")
    return Path.home() / ".claude" / "projects" / encoded


def find_transcript(cwd: str | os.PathLike) -> Path | None:
    d = project_dir_for_cwd(cwd)
    if not d.is_dir():
        return None
    files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def session_id_of(transcript: Path) -> str:
    """Claude Code names each transcript `<session-id>.jsonl`."""
    return transcript.stem


# ---- condensing ----

def _blocks(content) -> list[dict]:
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _text_of(content) -> str:
    if isinstance(content, str):
        return content.strip()
    parts = [b["text"] for b in _blocks(content)
             if b.get("type") == "text" and b.get("text")]
    return " ".join(parts).strip()


def condense(transcript: Path, max_chars: int = _MAX_CONDENSED_CHARS) -> Digest:
    d = Digest()
    lines: list[str] = []
    try:
        with open(transcript, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = obj.get("type")
                msg = obj.get("message")
                if not isinstance(msg, dict):
                    continue
                if t == "user":
                    txt = _text_of(msg.get("content"))
                    if txt:
                        d.user_prompts.append(txt)
                        lines.append("USER: " + txt)
                elif t == "assistant":
                    content = msg.get("content")
                    txt = _text_of(content)
                    if txt:
                        d.assistant_texts.append(txt)
                        lines.append("ASSISTANT: " + txt)
                    for b in _blocks(content):
                        if b.get("type") != "tool_use":
                            continue
                        name = str(b.get("name", "?"))
                        d.tool_counts[name] = d.tool_counts.get(name, 0) + 1
                        inp = b.get("input") or {}
                        if name in _FILE_TOOLS:
                            fp = inp.get("file_path") or inp.get("notebook_path")
                            if fp:
                                d.files.append(str(fp))
                        elif name == "Bash":
                            cmd = inp.get("command")
                            if cmd:
                                d.commands.append(str(cmd))
                        lines.append(f"[tool: {name}]")
    except OSError as e:
        log.warning("cannot read transcript %s: %s", transcript, e)

    d.files = list(dict.fromkeys(d.files))  # de-dupe, keep order
    condensed = "\n".join(lines)
    if len(condensed) > max_chars:
        condensed = "…(truncated)…\n" + condensed[-max_chars:]
    d.condensed = condensed
    return d


# ---- the /journal skill, run in the resumed session ----


def build_skill_command(session_id: str, project: str | None = None,
                        model: str = SKILL_MODEL,
                        tools: Sequence[str] = SKILL_TOOLS) -> list[str]:
    """Argv for a headless run of the journal skill inside `session_id`.

    The prompt goes on stdin, not argv: `--allowedTools` is variadic and would
    otherwise swallow a trailing positional prompt.
    """
    cmd = ["claude", "-p", "--resume", session_id,
           "--permission-mode", "acceptEdits"]
    if model:
        cmd += ["--model", model]
    if tools:
        cmd += ["--allowedTools", *tools]
    return cmd


def skill_prompt(project: str | None = None) -> str:
    return f"{SKILL_COMMAND} {project}".strip() if project else SKILL_COMMAND


# ---- driving the skill inside the *live* session (preferred path) ----
#
# Typing the command into the running session beats `--resume` on every axis:
# no shared session id (so no two writers on one transcript), no forked id
# polluting the project dir, and — the reason that matters — the skill spawns a
# fork of the conversation, which here is the real in-memory one rather than a
# replay from disk. The cost is that tmux `send-keys` is fire-and-forget, so we
# have to read completion back out of the transcript ourselves.

# How long to wait for a busy session to finish its current turn before giving
# up on the live path.
LIVE_IDLE_WAIT_S = int(os.environ.get("SMARTPANEL_JOURNAL_IDLE_WAIT") or 120)
# How long to wait for the transcript to grow after we press Enter. The TUI pops
# a slash-command autocomplete, which can swallow the first Enter.
LIVE_SUBMIT_WAIT_S = 12
_POLL_S = 0.5

# Outcomes of the live attempt. Only NOT_SUBMITTED and UNAVAILABLE are safe to
# retry via `--resume`: once the prompt is in, a second run would append a
# duplicate entry to the shared monthly log.
LIVE_OK = "ok"
LIVE_NOT_SUBMITTED = "not-submitted"
LIVE_TIMEOUT = "timeout"
LIVE_UNAVAILABLE = "unavailable"

_RETRYABLE = {LIVE_NOT_SUBMITTED, LIVE_UNAVAILABLE}

# Indexes into state.JOURNAL_STEPS, reported through a `Progress` callback so
# the panel can show which step a journaling session is on (SP-1601). The
# headless route reports the same indexes with `headless=True`, where step 1
# reads "resume headlessly" instead of "type /journal".
STEP_WAIT_IDLE, STEP_TYPE, STEP_SKILL, STEP_CLOSE, STEP_RECEIPT = range(5)

# progress(step, *, headless=False, failed=False). Called on the worker thread;
# the caller bounces it to the main loop.
Progress = Callable[..., None]

# Outcomes (SP-1602), the same strings as state.OUTCOME_*.
OUTCOME_JOURNALED = "journaled"
OUTCOME_SKILL_FAILED = "skill_failed"
OUTCOME_TIMEOUT = "timeout"


@dataclass(frozen=True)
class JournalResult:
    outcome: str
    path: Path | None = None
    confirmation: str = ""
    headless: bool = False

    @property
    def journaled(self) -> bool:
        return self.outcome == OUTCOME_JOURNALED


@dataclass(frozen=True)
class LiveResult:
    status: str
    text: str | None = None
    # The transcript the command was actually seen landing in, once known.
    transcript: Path | None = None

    @property
    def ok(self) -> bool:
        return self.status == LIVE_OK

    @property
    def retryable(self) -> bool:
        return self.status in _RETRYABLE


def classify_last_event(obj: dict | None) -> str:
    """Is the session idle (awaiting the user) or mid-turn?

    A turn ends with an assistant message whose content is text only. An
    assistant message carrying a `tool_use` block means a tool is about to run,
    and a `user` line mid-conversation is normally a `tool_result` feeding one
    back — both mean "still working".
    """
    if not isinstance(obj, dict):
        return "unknown"
    match obj.get("type"):
        case "assistant":
            msg = obj.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            kinds = {b.get("type") for b in _blocks(content)}
            if "tool_use" in kinds:
                return "working"
            # A thinking block is written as its own line ahead of the text
            # that ends the turn, so a thinking-only tail is mid-turn.
            return "working" if kinds == {"thinking"} else "idle"
        case "user":
            return "working"
        case _:
            return "unknown"


def is_conversation_line(obj: dict) -> bool:
    """Does this transcript line say anything about where the turn stands?

    Only main-thread user/assistant messages do. Claude Code writes bookkeeping
    after every turn (`system/turn_duration`, `tools-used`, `last-prompt`,
    `ai-title`, `mode`, `cost-state`, `queue-operation`, `attachment`,
    `api-request`, ...), so the literal last line of an idle session is almost
    never the assistant's answer. Sidechain (subagent) lines and meta user
    lines (expanded command bodies, caveats) are not turn boundaries either.
    """
    t = obj.get("type")
    if t not in ("assistant", "user") or obj.get("isSidechain"):
        return False
    return not (t == "user" and obj.get("isMeta"))


def tail_state(transcript: Path) -> tuple[int, str, str]:
    """(line count, idle/working, last assistant text) for a transcript.

    The count covers every line (any growth means the session wrote
    something); the state comes from the last *conversation* line only, see
    `is_conversation_line`.

    Cheap enough to poll: these files are appended a line at a time and stay
    well inside the size where a full read costs less than the poll interval.
    """
    lines = 0
    last_obj: dict | None = None
    last_text = ""
    try:
        with open(transcript, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                lines += 1
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict) or not is_conversation_line(obj):
                    continue
                last_obj = obj
                if obj.get("type") == "assistant":
                    msg = obj.get("message")
                    txt = _text_of(msg.get("content")) if isinstance(msg, dict) else ""
                    if txt:
                        last_text = txt
    except OSError as e:
        log.warning("cannot read transcript %s: %s", transcript, e)
        return 0, "unknown", ""
    return lines, classify_last_event(last_obj), last_text


def pane_idle(session_name: str) -> bool:
    """Is the session's TUI idle, read off its tmux pane (`activity`)?

    The transcript can't answer this when several sessions share a folder:
    the newest `*.jsonl` there may belong to a sibling.
    """
    from smartpanel.sessions.activity import IDLE, classify_pane
    return classify_pane(tmux.capture_pane(session_name)) == IDLE


def _mentions_prompt(text: str, prompt: str) -> bool:
    """Does a user line's text carry `prompt`, typed verbatim or as Claude
    Code records a slash command (`<command-name>/journal</command-name>
    <command-args>ledgerly</command-args>`)?"""
    if prompt in text:
        return True
    cmd, _, args = prompt.partition(" ")
    if f"<command-name>{cmd}</command-name>" not in text:
        return False
    return not args or f"<command-args>{args}</command-args>" in text


def typed_since(transcript: Path, skip: int, prompt: str) -> bool:
    """Did a user line carrying `prompt` land after the first `skip` lines?

    `skip` counts non-empty lines, as `tail_state` does.
    """
    seen = 0
    try:
        with open(transcript, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                seen += 1
                if seen <= skip:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict) or obj.get("type") != "user":
                    continue
                msg = obj.get("message")
                txt = _text_of(msg.get("content")) if isinstance(msg, dict) else ""
                if txt and _mentions_prompt(txt, prompt):
                    return True
    except OSError:
        return False
    return False


def _wait_until(predicate: Callable[[], bool], timeout_s: float,
                poll_s: float = _POLL_S, sleep=time.sleep,
                now=time.monotonic) -> bool:
    deadline = now() + timeout_s
    while True:
        if predicate():
            return True
        if now() >= deadline:
            return False
        sleep(poll_s)


def journal_in_session(session_name: str, transcript: Path,
                       project: str | None = None,
                       timeout_s: float = CLAUDE_TIMEOUT_S,
                       idle_wait_s: float = LIVE_IDLE_WAIT_S,
                       submit_wait_s: float = LIVE_SUBMIT_WAIT_S,
                       sleep=time.sleep,
                       progress: "Progress | None" = None, *,
                       ambiguous: bool = False) -> LiveResult:
    """Type the journal command into the live session and wait for the answer.

    Returns the skill's confirmation line on success. The caller kills the
    session only after this returns — that's the whole point of the live path.
    `progress(step)` is told when each of `state.JOURNAL_STEPS` 0-2 begins.

    `ambiguous` means another session shares this folder, so `transcript` (the
    newest file there) may be the sibling's: only its directory is trusted.
    Idle is then read off the pane, and the session's own transcript is the
    file in that directory where the typed command shows up.
    """
    report = progress or (lambda *_a, **_k: None)
    if not tmux.has_session(session_name):
        return LiveResult(LIVE_UNAVAILABLE)
    report(STEP_WAIT_IDLE)

    # 1. Don't interrupt a turn in progress: keys sent mid-turn just pile up in
    #    the input box and would be submitted along with whatever else is there.
    idle = ((lambda: pane_idle(session_name)) if ambiguous
            else (lambda: tail_state(transcript)[1] == "idle"))
    if not _wait_until(idle, idle_wait_s, sleep=sleep):
        log.warning("session %s never went idle; not sending %s",
                    session_name, SKILL_COMMAND)
        return LiveResult(LIVE_UNAVAILABLE)

    prompt = skill_prompt(project)
    project_dir = transcript.parent
    baselines = ({p: tail_state(p)[0] for p in project_dir.glob("*.jsonl")}
                 if ambiguous else {transcript: tail_state(transcript)[0]})
    mine: Path | None = None if ambiguous else transcript
    baseline = 0 if ambiguous else baselines[transcript]
    report(STEP_TYPE)
    log.info("sending %r to session %s", prompt, session_name)
    if not tmux.send_text(session_name, prompt):
        return LiveResult(LIVE_NOT_SUBMITTED)
    sleep(0.4)  # let the TUI's autocomplete settle before we submit
    if not tmux.send_enter(session_name):
        return LiveResult(LIVE_NOT_SUBMITTED)

    # 2. Confirm it actually went in. The slash-command autocomplete can eat the
    #    first Enter (accepting the completion instead of submitting), so nudge
    #    once before concluding nothing happened.
    def submitted() -> bool:
        nonlocal mine, baseline
        if not ambiguous:
            return tail_state(transcript)[0] > baseline
        for p in project_dir.glob("*.jsonl"):
            base = baselines.get(p, 0)  # a file created since counts from 0
            if typed_since(p, base, prompt):
                mine, baseline = p, base
                return True
        return False

    if not _wait_until(submitted, submit_wait_s / 2, sleep=sleep):
        log.info("no transcript growth yet; nudging Enter in %s", session_name)
        tmux.send_enter(session_name)
        if not _wait_until(submitted, submit_wait_s / 2, sleep=sleep):
            log.warning("%s never reached the transcript of %s",
                        SKILL_COMMAND, session_name)
            return LiveResult(LIVE_NOT_SUBMITTED)

    # 3. The command is in, so from here we must never fall back to --resume:
    #    a second run would duplicate the entry in the shared monthly log.
    report(STEP_SKILL)
    if mine is None:  # unreachable: submitted() sets it before returning True
        return LiveResult(LIVE_NOT_SUBMITTED)

    def finished() -> bool:
        count, state, _ = tail_state(mine)
        return count > baseline and state == "idle"

    if not _wait_until(finished, timeout_s, sleep=sleep):
        log.warning("%s did not finish in %ss in session %s",
                    SKILL_COMMAND, timeout_s, session_name)
        return LiveResult(LIVE_TIMEOUT, transcript=mine)

    return LiveResult(LIVE_OK, tail_state(mine)[2] or None, transcript=mine)


# The skill appends to a single shared file (one per month), so concurrent runs
# — "Close all" journals every session at once — would clobber each other.
_SKILL_LOCK = threading.Lock()


def run_journal_skill(session_id: str, cwd: str | os.PathLike | None,
                      project: str | None = None) -> str | None:
    """Resume the session and run the journal skill in it.

    Returns the skill's confirmation line, or None if it could not run. Runs
    with `cwd` as the working directory so the skill's git sweep lands in the
    right repo. Serialized across threads — see `_SKILL_LOCK`.
    """
    with _SKILL_LOCK:
        return _run_journal_skill(session_id, cwd, project)


def _run_journal_skill(session_id: str, cwd: str | os.PathLike | None,
                       project: str | None) -> str | None:
    cmd = build_skill_command(session_id, project)
    log.info("running journal skill: %s", shlex.join(cmd))
    try:
        r = subprocess.run(
            cmd,
            input=skill_prompt(project),
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT_S,
            cwd=str(cwd) if cwd else None,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
        log.warning("journal skill failed (rc=%s): %s", r.returncode,
                    (r.stderr or "").strip()[:300])
    except FileNotFoundError:
        log.warning("`claude` not found; writing the Activity section only")
    except subprocess.TimeoutExpired:
        log.warning("journal skill timed out after %ss", CLAUDE_TIMEOUT_S)
    except OSError as e:
        log.warning("journal skill error: %s", e)
    return None


# ---- rendering + writing ----

def _once(fn: Callable[[], None] | None) -> Callable[[], None]:
    """Wrap `fn` so it runs at most once. Closing twice would log a spurious
    "no such session" on the second kill."""
    done = threading.Event()

    def call() -> None:
        if fn is None or done.is_set():
            return
        done.set()
        fn()

    return call


def _fmt_duration(seconds: float | None) -> str:
    if not seconds or seconds <= 0:
        return "unknown"
    s = int(seconds)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name) or "session"


def _render_markdown(project: str, cwd, transcript: Path | None, digest: Digest | None,
                     duration_s: float | None, when: datetime,
                     skill_result: str | None,
                     no_digest_note: str = "No transcript found for this folder.") -> str:
    out = [f"# Session journal — {project}", ""]
    out.append(f"- **When:** {when:%Y-%m-%d %H:%M:%S}")
    out.append(f"- **Duration:** {_fmt_duration(duration_s)}")
    out.append(f"- **Folder:** `{cwd}`")
    if transcript:
        out.append(f"- **Transcript:** `{transcript}`")
        out.append(f"- **Session:** `{session_id_of(transcript)}`")
    out.append("")

    out.append(f"## `{SKILL_COMMAND}`")
    out.append(skill_result.strip() if skill_result
               else f"_(`{SKILL_COMMAND}` did not run — see Activity below.)_")
    out.append("")

    out.append("## Activity")
    if digest is None:
        out.append(f"_({no_digest_note})_")
        return "\n".join(out) + "\n"

    out.append(f"- Prompts: {len(digest.user_prompts)} · "
               f"Assistant replies: {len(digest.assistant_texts)}")
    if digest.tool_counts:
        tools = ", ".join(f"{k}×{v}" for k, v in
                          sorted(digest.tool_counts.items(), key=lambda kv: -kv[1]))
        out.append(f"- Tools: {tools}")
    if digest.files:
        out.append("")
        out.append("**Files touched:**")
        out += [f"- `{f}`" for f in digest.files[:50]]
        if len(digest.files) > 50:
            out.append(f"- …and {len(digest.files) - 50} more")
    if digest.commands:
        out.append("")
        out.append("**Commands run:**")
        out += [f"- `{c.splitlines()[0][:120]}`" for c in digest.commands[:40]]
        if len(digest.commands) > 40:
            out.append(f"- …and {len(digest.commands) - 40} more")
    return "\n".join(out) + "\n"


FAILED_CONFIRMATION = (f"The {SKILL_COMMAND} skill returned no confirmation line. The session "
                       "was still closed; this receipt holds the Activity section only.")
SHARED_CWD_CONFIRMATION = (f"Several sessions share this folder and {SKILL_COMMAND} could not "
                           "run in the live session, so no headless journal was attempted: "
                           "resuming the newest transcript could journal the wrong conversation.")
NO_TRANSCRIPT_CONFIRMATION = (f"No transcript was found for this folder, so {SKILL_COMMAND} "
                              "could not run.")


def _front_matter(*, when: datetime, session: str, title: str, project: str,
                  outcome: str, duration_s: float | None, digest: Digest | None,
                  confirmation: str, cwd, tmux_name: str | None) -> str:
    """Machine-readable header for the receipt index (SP-1801).

    YAML between `---` fences, ahead of the unchanged Markdown body, so the
    file still reads fine in any editor and old receipts keep parsing.
    """
    import yaml

    meta = {
        "time": when.isoformat(timespec="seconds"),
        "session": session,
        "tmux": tmux_name or "",
        "title": title,
        "project": project,
        "cwd": str(cwd) if cwd else "",
        "outcome": outcome,
        "duration_s": int(duration_s) if duration_s and duration_s > 0 else None,
        "files": list(digest.files) if digest else [],
        "commands": [c.splitlines()[0][:200] for c in digest.commands if c.strip()]
        if digest else [],
        "tools": dict(digest.tool_counts) if digest else {},
        "confirmation": confirmation,
    }
    body = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=10_000)
    return f"---\n{body}---\n\n"


def run_journal(project: str, cwd: str | os.PathLike | None,
                duration_s: float | None = None, run_skill: bool = True,
                session_name: str | None = None,
                close_session: Callable[[], None] | None = None, *,
                title: str = "", on_progress: Progress | None = None,
                journal_dir: Path | None = None,
                ambiguous: bool = False) -> JournalResult:
    """Journal the session rooted at `cwd`, then close it. Returns the outcome.

    Two routes to the same skill. If the tmux session is still alive we type the
    command into it and close only once it answers. Otherwise we fall back to
    resuming the transcript headlessly, which *requires* the session to be dead
    first — `--resume` reuses the session id, so a live session would leave two
    processes appending to one transcript.

    `close_session` is invoked at whichever point is correct for the route
    taken; it is always invoked exactly once, even if journaling fails, so a
    session can never be left un-closable.

    `ambiguous`: another session shares `cwd`, so the newest transcript there
    may be a sibling's. Only the live route runs (it finds the right file by
    where the typed command lands); the headless fallback, which can only
    resume by cwd, is refused, and the receipt's Activity section is left out
    unless the live run identified the transcript.

    `on_progress(step, headless=, failed=)` reports `state.JOURNAL_STEPS`
    indexes, always increasing. A failure is reported once, on the furthest
    step the run actually began (step 0 when there was no transcript to
    journal, step 1 when the command never reached the session, otherwise the
    skill step), and the run then closes and writes the receipt without
    advancing: the UI shows "failed, closing anyway" on the step that failed
    rather than ticking steps that never happened.
    """
    reached = STEP_WAIT_IDLE

    def progress(step: int, **kw) -> None:
        nonlocal reached
        if not kw.get("failed"):
            reached = max(reached, step)
        if on_progress is None:
            return
        try:
            on_progress(step, **kw)
        except Exception as e:  # noqa: BLE001 — a UI hiccup must not stop the close
            log.debug("journal progress callback failed: %s", e)

    when = datetime.now()
    transcript = find_transcript(cwd) if cwd else None
    skill_result: str | None = None
    live = LiveResult(LIVE_UNAVAILABLE)
    headless = False
    failed = False

    progress(STEP_WAIT_IDLE)
    if run_skill and transcript and session_name:
        with _SKILL_LOCK:  # the skill appends to one shared monthly file
            live = journal_in_session(session_name, transcript, project,
                                      progress=progress, ambiguous=ambiguous)
        skill_result = live.text
        if live.ok:
            progress(STEP_CLOSE)
    found = transcript
    if ambiguous:
        # The newest file is only a guess; trust what the live run saw.
        transcript = live.transcript

    if close_session is not None:
        try:
            close_session()
        except Exception as e:  # noqa: BLE001 — a failed kill must not lose the journal
            log.warning("failed to close session %s: %s", session_name, e)

    # Only retry headlessly when we know the prompt never landed. After a
    # timeout the skill may still be running in there; a second pass would
    # append a duplicate entry.
    if run_skill and transcript and live.retryable and not ambiguous:
        headless = True
        progress(STEP_TYPE, headless=True)
        progress(STEP_SKILL, headless=True)
        skill_result = run_journal_skill(session_id_of(transcript), cwd, project)
        if skill_result:
            progress(STEP_CLOSE, headless=True)
    elif live.status == LIVE_TIMEOUT:
        skill_result = (f"_(`{SKILL_COMMAND}` was submitted to the live session but "
                        f"had not finished after {CLAUDE_TIMEOUT_S}s.)_")

    if live.status == LIVE_TIMEOUT:
        outcome, failed = OUTCOME_TIMEOUT, True
        confirmation = (f"{SKILL_COMMAND} was submitted to the live session but had not "
                        f"finished after {CLAUDE_TIMEOUT_S}s.")
    elif live.ok or (headless and skill_result):
        outcome, confirmation = OUTCOME_JOURNALED, (skill_result or "").strip()
    else:
        outcome, failed = OUTCOME_SKILL_FAILED, True
        if ambiguous and run_skill and session_name and found:
            confirmation = SHARED_CWD_CONFIRMATION
        elif transcript or not run_skill:
            confirmation = FAILED_CONFIRMATION
        else:
            confirmation = NO_TRANSCRIPT_CONFIRMATION
    if failed:
        progress(min(reached, STEP_SKILL), headless=headless, failed=True)

    # Condensed after the close so the receipt covers the whole session,
    # including the journal turn itself.
    digest = condense(transcript) if transcript else None
    if not failed:
        progress(STEP_RECEIPT, headless=headless)

    directory = journal_dir or JOURNAL_DIR
    path: Path | None = directory / f"{when:%Y%m%d-%H%M%S}-{_safe(project)}.md"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        header = _front_matter(
            when=when, session=session_id_of(transcript) if transcript else "",
            title=title, project=project, outcome=outcome, duration_s=duration_s,
            digest=digest, confirmation=confirmation, cwd=cwd, tmux_name=session_name)
        path.write_text(
            header + _render_markdown(
                project, cwd, transcript, digest, duration_s, when, skill_result,
                no_digest_note=("Several sessions share this folder and this one's "
                                "transcript could not be told apart."
                                if ambiguous and found and not transcript
                                else "No transcript found for this folder.")),
            encoding="utf-8",
        )
        log.info("wrote journal %s", path)
    except Exception as e:  # noqa: BLE001 — OSError, or YAML on odd input
        log.warning("failed to write journal %s: %s", path, e)
        path = None
    return JournalResult(outcome, path, confirmation, headless)
