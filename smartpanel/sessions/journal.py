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

Both steps are slow, so callers use `write_journal_async`, which runs on a
daemon thread off the GLib main loop.
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


@dataclass(frozen=True)
class LiveResult:
    status: str
    text: str | None = None

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
            has_tool = any(b.get("type") == "tool_use" for b in _blocks(content))
            return "working" if has_tool else "idle"
        case "user":
            return "working"
        case _:
            return "unknown"


def tail_state(transcript: Path) -> tuple[int, str, str]:
    """(line count, idle/working, last assistant text) for a transcript.

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
                       sleep=time.sleep) -> LiveResult:
    """Type the journal command into the live session and wait for the answer.

    Returns the skill's confirmation line on success. The caller kills the
    session only after this returns — that's the whole point of the live path.
    """
    if not tmux.has_session(session_name):
        return LiveResult(LIVE_UNAVAILABLE)

    # 1. Don't interrupt a turn in progress: keys sent mid-turn just pile up in
    #    the input box and would be submitted along with whatever else is there.
    if not _wait_until(lambda: tail_state(transcript)[1] == "idle",
                       idle_wait_s, sleep=sleep):
        log.warning("session %s never went idle; not sending %s",
                    session_name, SKILL_COMMAND)
        return LiveResult(LIVE_UNAVAILABLE)

    baseline, _, _ = tail_state(transcript)
    prompt = skill_prompt(project)
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
        return tail_state(transcript)[0] > baseline

    if not _wait_until(submitted, submit_wait_s / 2, sleep=sleep):
        log.info("no transcript growth yet; nudging Enter in %s", session_name)
        tmux.send_enter(session_name)
        if not _wait_until(submitted, submit_wait_s / 2, sleep=sleep):
            log.warning("%s never reached the transcript of %s",
                        SKILL_COMMAND, session_name)
            return LiveResult(LIVE_NOT_SUBMITTED)

    # 3. The command is in, so from here we must never fall back to --resume:
    #    a second run would duplicate the entry in the shared monthly log.
    def finished() -> bool:
        count, state, _ = tail_state(transcript)
        return count > baseline and state == "idle"

    if not _wait_until(finished, timeout_s, sleep=sleep):
        log.warning("%s did not finish in %ss in session %s",
                    SKILL_COMMAND, timeout_s, session_name)
        return LiveResult(LIVE_TIMEOUT)

    return LiveResult(LIVE_OK, tail_state(transcript)[2] or None)


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
                     skill_result: str | None) -> str:
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
        out.append("_(No transcript found for this folder.)_")
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


def write_journal(project: str, cwd: str | os.PathLike | None,
                  duration_s: float | None = None, run_skill: bool = True,
                  session_name: str | None = None,
                  close_session: Callable[[], None] | None = None) -> Path | None:
    """Journal the session rooted at `cwd`, then close it.

    Two routes to the same skill. If the tmux session is still alive we type the
    command into it and close only once it answers. Otherwise we fall back to
    resuming the transcript headlessly, which *requires* the session to be dead
    first — `--resume` reuses the session id, so a live session would leave two
    processes appending to one transcript.

    `close_session` is invoked at whichever point is correct for the route
    taken; it is always invoked exactly once, even if journaling fails, so a
    session can never be left un-closable. Returns the receipt's path.
    """
    when = datetime.now()
    transcript = find_transcript(cwd) if cwd else None
    skill_result: str | None = None
    live = LiveResult(LIVE_UNAVAILABLE)

    if run_skill and transcript and session_name:
        with _SKILL_LOCK:  # the skill appends to one shared monthly file
            live = journal_in_session(session_name, transcript, project)
        skill_result = live.text

    if close_session is not None:
        try:
            close_session()
        except Exception as e:  # noqa: BLE001 — a failed kill must not lose the journal
            log.warning("failed to close session %s: %s", session_name, e)

    # Only retry headlessly when we know the prompt never landed. After a
    # timeout the skill may still be running in there; a second pass would
    # append a duplicate entry.
    if run_skill and transcript and live.retryable:
        skill_result = run_journal_skill(session_id_of(transcript), cwd, project)
    elif live.status == LIVE_TIMEOUT:
        skill_result = (f"_(`{SKILL_COMMAND}` was submitted to the live session but "
                        f"had not finished after {CLAUDE_TIMEOUT_S}s.)_")

    # Condensed after the close so the receipt covers the whole session,
    # including the journal turn itself.
    digest = condense(transcript) if transcript else None

    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    path = JOURNAL_DIR / f"{when:%Y%m%d-%H%M%S}-{_safe(project)}.md"
    try:
        path.write_text(
            _render_markdown(project, cwd, transcript, digest, duration_s, when,
                             skill_result),
            encoding="utf-8",
        )
    except OSError as e:
        log.warning("failed to write journal %s: %s", path, e)
        return None
    log.info("wrote journal %s", path)
    return path


def write_journal_async(project: str, cwd: str | os.PathLike | None,
                        duration_s: float | None = None, run_skill: bool = True,
                        session_name: str | None = None,
                        close_session: Callable[[], None] | None = None,
                        on_done: Callable[[Path | None], None] | None = None) -> None:
    """Generate the journal on a daemon thread (the skill run is slow)."""
    once = _once(close_session)

    def run() -> None:
        p = None
        try:
            p = write_journal(project, cwd, duration_s, run_skill,
                              session_name, once)
        except Exception as e:  # noqa: BLE001 — never let the worker thread die loudly
            log.warning("journal generation failed for %s: %s", project, e)
        finally:
            # write_journal normally owns the close; if it raised before getting
            # there the session would be stranded, so backstop it here. `_once`
            # makes the second call a no-op.
            once()
        if on_done is not None:
            on_done(p)

    threading.Thread(target=run, name="smartpanel-journal", daemon=True).start()
