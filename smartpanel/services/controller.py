"""Real implementation of `state.actions.Actions` + the session refresh loop.

API (fixed by the v2 contract):
    Controller(store, config, spend=None)   # spend: SpendService for per-session cost
    .start() / .stop()                       # 2 s session refresh, receipt index
    + every method of state.actions.Actions

Keyword-only extras after `spend` exist for tests (fake tmux runner, fake loop,
tmp journal dir); production passes none of them.

Every action returns at once. Anything that can block — focusing (gdbus and
friends, up to 3 s each), answering a prompt (a 2 s confirmation), journaling
(minutes) — runs on a worker thread, and its result comes back through
`loop.idle` so only the main thread touches the Store. Feedback is Events
(toasts, dialogs), never return values, so a D-Bus caller and a GTK caller see
the same thing.
"""
from __future__ import annotations

import inspect
import logging
import time
from pathlib import Path
from typing import Any, Callable

from smartpanel.services.sessions import SessionsService, default_loop
from smartpanel.sessions import focus as focus_mod
from smartpanel.sessions import journal, launch as launch_mod, prompt as prompt_mod, tmux
from smartpanel.sessions.receipts import ReceiptIndex, monthly_log_path
from smartpanel.state import Event
from smartpanel.state.model import (
    OUTCOME_JOURNALED,
    PHASE_JOURNALING,
    PHASE_LAUNCHING,
    Loading,
    Session,
)
from smartpanel.uiformat import (
    focus_message,
    format_minutes,
    home_path,
    session_title,
    terminal_display_name,
)

log = logging.getLogger(__name__)

DEFAULT_MIN_AGE_S = 120  # config default for sessions.min_age_s


def launch_default_uri(uri: str, done: Callable[[bool], None]) -> None:
    """Hand `uri` to its default app without blocking the main loop
    (`launch_default_for_uri_async` resolves and spawns off the loop, possibly via
    the portal); `done(ok)` runs from its callback."""
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    def finish(_src, result) -> None:
        try:
            ok = bool(Gio.AppInfo.launch_default_for_uri_finish(result))
        except Exception:  # noqa: BLE001
            ok = False
        done(ok)

    Gio.AppInfo.launch_default_for_uri_async(uri, None, None, finish)


class Controller:
    def __init__(self, store, config, spend=None, *, run: tmux.Runner | None = None,
                 loop=None, clock: Callable[[], float] = time.time,
                 which: Callable[[str], str | None] | None = None,
                 journal_dir: Path | None = None,
                 focus: Callable[..., str] | None = None,
                 launcher: Callable[..., launch_mod.LaunchResult] | None = None,
                 answerer: Callable[..., prompt_mod.AnswerResult] | None = None,
                 journal_runner: Callable[..., journal.JournalResult] | None = None,
                 notifier: Callable[[str, str, str, dict | None], None] | None = None,
                 find_transcript: Callable[[str], Path | None] | None = None,
                 opener: Callable[[str, Callable[[bool], None]], None] | None = None):
        self.store, self.config, self.spend = store, config, spend
        self.loop = loop if loop is not None else default_loop()
        self.clock = clock
        self.run = run or tmux._run
        self.journal_dir = journal_dir
        self.sessions = SessionsService(store, config, spend, run=self.run, loop=self.loop,
                                        clock=clock, which=which,
                                        find_transcript=find_transcript)
        self.receipts = ReceiptIndex(journal_dir)
        self._focus = focus or focus_mod.focus_session
        self._launch = launcher or launch_mod.launch
        self._answer = answerer or prompt_mod.answer
        self._run_journal = journal_runner or journal.run_journal
        self._notifier = notifier
        self._opener = opener or launch_default_uri
        self._answering: set[str] = set()
        self._focusing: set[str] = set()
        self._closing_all = False
        self._unsub_config: Callable[[], None] | None = None
        self.last_focus_result: str | None = None

    # ── lifecycle ──
    def start(self) -> None:
        self.sessions.start()
        self.reindex_journal()
        try:
            self._unsub_config = self.config.subscribe(self._on_config)
        except Exception:  # noqa: BLE001
            self._unsub_config = None

    def stop(self) -> None:
        self.sessions.stop()
        if self._unsub_config:
            self._unsub_config()
            self._unsub_config = None

    def _on_config(self, key: str, _value: Any) -> None:
        if key == "sessions.terminal":
            self.sessions.check_environment()

    # ── plumbing ──
    def _post(self, fn: Callable[[], object]) -> None:
        """Run `fn` on the main loop (or right away without one, e.g. headless)."""
        if self.loop is not None:
            self.loop.idle(fn)
        else:
            fn()

    def _spawn(self, fn: Callable[[], None], name: str) -> None:
        if self.loop is not None:
            self.loop.thread(fn, name)
        else:
            fn()

    def _session(self, name: str) -> Session | None:
        s = self.sessions.find(name)
        if s is None:
            self.store.toast(f"No session named “{name}”.", error=True)
        return s

    def _busy(self, s: Session) -> bool:
        if s.phase == PHASE_JOURNALING:
            self.store.toast(f"“{session_title(s)}” is being journaled and closed.")
            return True
        if s.phase == PHASE_LAUNCHING:
            self.store.toast(f"“{session_title(s)}” is still starting.")
            return True
        return False

    def _journal_on(self) -> bool:
        return bool(self.config.get("sessions.journal")) and bool(journal.SKILL_COMMAND)

    def _min_age(self) -> float:
        try:
            return float(self.config.get("sessions.min_age_s") or 0)
        except (TypeError, ValueError):
            return float(DEFAULT_MIN_AGE_S)

    def _journal_reason(self, s: Session) -> str | None:
        """Why this session closes without a journal (SP-1609), or None if it gets one."""
        if not self._journal_on():
            return ""
        if not s.created or self.clock() - s.created < self._min_age():
            return f"Open under {format_minutes(self._min_age())}, so no journal."
        return None

    # ── navigation / surfaces ──
    def open_dashboard(self, page: str = "") -> None:
        self.store.emit(Event("open_dashboard", {"page": page}))

    def open_palette(self) -> None:
        self.store.emit(Event("open_palette", {}))

    def set_hud(self, enabled: bool) -> None:
        self.set_setting("hud.enabled", bool(enabled))

    def pin_ticker(self, pinned: bool) -> None:
        self.store.update("app", ticker_pinned=bool(pinned))

    # ── focus (SP-1701/1702) ──
    def focus_session(self, name: str, on_result: Callable[[str], None] | None = None) -> None:
        s = self._session(name)
        if s is None or self._busy(s) or name in self._focusing:
            # Unknown, journaling/launching, or already being focused: no
            # window was raised. A caller waiting on the result still gets one.
            if on_result is not None:
                self._post(lambda: self._report_focus(on_result, focus_mod.FAILED))
            return
        self._focusing.add(name)
        heading = self.sessions.heading(name)
        title = session_title(s)
        reattach = bool(self.config.get("sessions.reattach"))
        term = self.sessions.terminal()

        def work() -> None:
            try:
                result = self._focus(name, heading, term=term, reattach=reattach)
            except Exception:  # noqa: BLE001
                log.exception("focus failed for %s", name)
                result = focus_mod.FAILED
            self._post(lambda: self._focus_done(name, title, result, reattach, on_result))

        self._spawn(work, "smartpanel-focus")

    def _focus_done(self, name, title, result, reattach, on_result) -> None:
        self._focusing.discard(name)
        self.last_focus_result = result
        text, error, action = focus_message(result, title, reattach)
        self.store.toast(text, error=error, action=action)
        if result == focus_mod.REATTACHED:
            # The old window went with its client; re-push the title next tick.
            self.sessions.pushed_titles.pop(name, None)
        self.sessions.refresh()  # `attached` may have changed
        if on_result is not None:
            self._report_focus(on_result, result)

    @staticmethod
    def _report_focus(on_result: Callable[[str], None], result: str) -> None:
        try:
            on_result(result)
        except Exception:  # noqa: BLE001
            log.exception("focus result callback failed")

    def retitle_session(self, name: str) -> None:
        s = self._session(name)
        if s is None or self._busy(s):
            return
        self.sessions.retitle(name)
        self.store.toast("Title re-read from tmux")

    # ── files ──
    def open_path(self, path: str) -> None:
        full = Path(str(path or "")).expanduser()
        name = full.name or str(path)

        def failed() -> None:
            self.store.toast(f"Couldn’t open {name}", error=True)

        if not path or not full.exists():
            failed()
            return

        def done(ok: bool) -> None:
            if not ok:
                log.warning("could not open %s", full)
                self._post(failed)
        try:
            self._opener(full.as_uri(), done)
        except Exception:  # noqa: BLE001 — never raise into the loop
            log.exception("open_path %s failed", full)
            failed()

    # ── close & journal (SP-1601..1603, SP-1609) ──
    def request_close(self, name: str) -> None:
        s = self._session(name)
        if s is None or self._busy(s):
            return
        if self._journal_reason(s) is None:
            self.store.emit(Event("confirm_close", {"name": name}))
        else:
            self.close_session(name, journal=False)

    def close_session(self, name: str, journal: bool) -> None:  # noqa: A002 — Actions API
        s = self._session(name)
        if s is None or self._busy(s):
            return
        reason = self._journal_reason(s)
        if reason is not None:          # too young, or journaling is off: say why
            self._close_now(s, reason)
            return
        if not journal:                 # the user chose "Close without"
            self._close_now(s, "")
            return
        cwd = s.cwd or tmux.session_cwd(name, run=self.run)  # before any kill
        self.sessions.begin_journal(name)
        self._spawn(lambda: self._journal_worker([(s, cwd)]), "smartpanel-journal")

    def _close_now(self, s: Session, reason: str | None) -> None:
        tmux.kill_session(s.name, run=self.run)
        self.sessions.refresh()
        tail = f" {reason}" if reason else ""
        self.store.toast(f"Closed “{session_title(s)}”.{tail}")

    def request_close_all(self) -> None:
        items = [s for s in self.store.state.sessions.items
                 if s.phase not in (PHASE_JOURNALING, PHASE_LAUNCHING)]
        if not items:
            self.store.toast("No sessions to close", error=True)
            return
        journaled = sum(1 for s in items if self._journal_reason(s) is None)
        self.store.emit(Event("confirm_close_all", {"count": len(items),
                                                    "journaled": journaled}))

    def close_all(self) -> None:
        if self._closing_all:
            self.store.toast("Already closing all sessions.")
            return
        items = [s for s in self.store.state.sessions.items
                 if s.phase not in (PHASE_JOURNALING, PHASE_LAUNCHING)]
        if not items:
            self.store.toast("No sessions to close", error=True)
            return
        young = [s for s in items if self._journal_reason(s) is not None]
        worth = [s for s in items if self._journal_reason(s) is None]
        # Short sessions close at once, without a journal (SP-1603).
        for s in young:
            tmux.kill_session(s.name, run=self.run)
        if not worth:
            self.sessions.refresh()
            self.store.toast("All sessions closed")
            return
        queue = [(s, s.cwd or tmux.session_cwd(s.name, run=self.run)) for s in worth]
        self._closing_all = True
        self.store.update("sessions", close_all=Loading(done=len(young), total=len(items)))
        for s, _ in queue:
            self.sessions.begin_journal(s.name)
        self._spawn(lambda: self._journal_worker(queue, close_all=True), "smartpanel-close-all")

    def _journal_worker(self, queue: list[tuple[Session, str | None]],
                        close_all: bool = False) -> None:
        """Worker thread: journal each session in turn, then close it.

        Sequential on purpose — the skill appends to one shared monthly log, and
        `_SKILL_LOCK` would serialize the runs anyway. Every guarantee of
        `journal.write_journal_async` holds here too: `_once` around the kill,
        and a `finally` backstop so a session is never stranded un-closable.
        """
        failures = 0
        for s, cwd in queue:
            name = s.name
            once = journal._once(lambda n=name: tmux.kill_session(n, run=self.run))
            result = journal.JournalResult(journal.OUTCOME_SKILL_FAILED, None,
                                           journal.FAILED_CONFIRMATION)
            try:
                result = self._run_journal(
                    s.project or name, cwd,
                    duration_s=max(0.0, self.clock() - s.created) if s.created else None,
                    run_skill=True, session_name=name, close_session=once,
                    title=session_title(s),
                    on_progress=lambda step, n=name, **kw: self._post(
                        lambda: self.sessions.journal_progress(n, step, **kw)),
                    journal_dir=self.journal_dir)
            except Exception:  # noqa: BLE001
                log.exception("journal failed for %s", name)
            finally:
                once()
            if not result.journaled:
                failures += 1
            self._post(lambda s=s, r=result: self._journal_done(s, r, close_all))
        if close_all:
            self._post(lambda: self._close_all_done(failures))

    def _journal_done(self, s: Session, result: journal.JournalResult, close_all: bool) -> None:
        self.sessions.end_journal(s.name)
        self.reindex_journal()
        title = session_title(s)
        entry_id = result.path.stem if result.path else None
        action = {"label": "View", "page": f"journal:{entry_id}"} if entry_id else None
        if result.outcome == OUTCOME_JOURNALED:
            log_name = monthly_log_path()
            where = f"Appended to {Path(log_name).parent.name}/{Path(log_name).name}" \
                if log_name else "Journal entry written"
            self._notify("journal", f"Journal written · {title}", f"{where}. Session closed.",
                         action)
            self.store.toast(f"Journaled & closed “{title}”", action=action)
        else:
            self._notify("journal_failed", f"Journal failed · {title}",
                         "The session was closed anyway. A receipt with its activity was saved.",
                         action)
            self.store.toast("Closed. The journal failed, receipt saved.", error=True,
                             action=action)
        if close_all:
            cur = self.store.state.sessions.close_all
            if cur is not None:
                self.store.update("sessions", close_all=Loading(done=min(cur.total, cur.done + 1),
                                                                 total=cur.total))

    def _close_all_done(self, failures: int) -> None:
        self._closing_all = False
        self.store.update("sessions", close_all=None)
        self.sessions.refresh()
        if failures:
            self.store.toast(f"All sessions closed. {failures} journal"
                             f"{'s' if failures > 1 else ''} failed, receipts saved.",
                             error=True, action={"label": "View", "page": "journal"})
        else:
            self.store.toast("All sessions closed")

    def _notify(self, kind: str, title: str, body: str, action: dict | None) -> None:
        """Desktop notification, tolerant of both `notify.send` signatures."""
        try:
            if self._notifier is not None:
                self._notifier(kind, title, body, action)
                return
            from smartpanel import notify
            send = getattr(notify, "send", None)
            if send is None:
                return
            params = list(inspect.signature(send).parameters)
            if params and params[0] == "kind":
                # notify.KINDS names the success kind "journal_ok"; the bubble's
                # button must be a notify.Action whose callback routes the page.
                v2_kind = "journal_ok" if kind == "journal" else kind
                button = None
                if action and hasattr(notify, "Action"):
                    page = action.get("page", "journal")
                    button = notify.Action(action.get("label", "View"),
                                           lambda p=page: self.store.emit(
                                               Event("open_dashboard", {"page": p})))
                send(v2_kind, title, body, action=button)
            else:  # the original send(summary, body, urgency, key)
                send(title, body, urgency="critical" if kind.endswith("failed") else "normal",
                     key=f"smartpanel-{kind}")
        except Exception as e:  # noqa: BLE001 — a notification must never break a close
            log.warning("journal notification failed: %s", e)

    # ── journal history (SP-1802) ──
    def reindex_journal(self) -> None:
        def work() -> None:
            entries = self.receipts.scan()
            monthly = monthly_log_path()
            self._post(lambda: self.store.update("journal", entries=entries,
                                                 monthly_log_path=monthly))
        self._spawn(work, "smartpanel-receipts")

    # ── new session (SP-1501/1502) ──
    def request_new_session(self) -> None:
        if not self.store.state.sessions.tmux_ok:
            self.store.toast("Install tmux first", error=True)
            self.open_dashboard("sessions")
            return
        self.store.emit(Event("new_session", {}))

    def new_session(self, directory: str) -> None:
        term = self.sessions.terminal()
        try:
            result = self._launch(directory, term)
        except Exception as e:  # noqa: BLE001
            result = launch_mod.LaunchResult(launch_mod.FAILED, str(e), str(directory))
        if result.status == launch_mod.STARTED:
            self.sessions.add_launch(result.directory or directory)
        elif result.status == launch_mod.TERMINAL_MISSING:
            self.store.update("sessions", terminal_ok=False)
            self.store.toast(f"Couldn’t open {terminal_display_name(term)}. Install it, "
                             "or set SMARTPANEL_TERMINAL.", error=True,
                             action={"label": "Settings", "page": "settings"})
        elif result.status == launch_mod.TMUX_MISSING:
            self.store.update("sessions", tmux_ok=False)
            self.store.toast("Install tmux first", error=True)
            self.open_dashboard("sessions")
        else:
            self.store.toast(f"Couldn’t start a session in {home_path(str(directory))}: "
                             f"{result.message}", error=True)
        return None

    def list_start_folders(self) -> list[dict]:
        base = Path(str(self.config.get("sessions.dir") or "~")).expanduser()
        try:
            dirs = [d for d in base.iterdir() if d.is_dir() and not d.name.startswith(".")]
        except OSError as e:
            log.warning("cannot list %s: %s", base, e)
            return []
        cwds = [c for c in self.sessions.cwds.values() if c]
        out = []
        for d in sorted(dirs, key=lambda p: (p.name.casefold(), p.name)):
            path = str(d)
            out.append({"name": d.name, "path": path,
                        "has_session": any(c == path or c.startswith(path + "/")
                                           for c in cwds)})
        return out

    # ── prompts (SP-1303, SP-1306) ──
    def answer_prompt(self, name: str, option: int, fingerprint: str) -> None:
        s = self._session(name)
        if s is None or self._busy(s) or name in self._answering:
            return
        self._answering.add(name)
        title = session_title(s)

        def work() -> None:
            try:
                result = self._answer(name, int(option), fingerprint, run=self.run)
            except Exception:  # noqa: BLE001
                log.exception("answer failed for %s", name)
                result = prompt_mod.AnswerResult.FAILED
            self._post(lambda: self._answer_done(name, title, int(option), result))

        self._spawn(work, "smartpanel-answer")

    def _answer_done(self, name: str, title: str, option: int,
                     result: prompt_mod.AnswerResult) -> None:
        self._answering.discard(name)
        open_action = {"label": "Open", "page": "sessions", "focus": name}
        if result == prompt_mod.AnswerResult.SENT:
            self.store.toast(f"Sent “{option + 1}” to {title}")
        elif result == prompt_mod.AnswerResult.PROMPT_CHANGED:
            self.store.toast("The prompt changed. Open the terminal to answer.",
                             error=True, action=open_action)
        elif result == prompt_mod.AnswerResult.FAILED:
            self.store.toast(f"Couldn’t answer “{title}”. Open the terminal to answer.",
                             error=True, action=open_action)
        # NOT_WAITING: refresh quietly.
        self.sessions.refresh()

    # ── settings / environment ──
    def set_setting(self, key: str, value: Any) -> None:
        try:
            self.config.set(key, value)
        except Exception as e:  # noqa: BLE001 — validation errors become a toast
            self.store.toast(f"Couldn’t save {key}: {e}", error=True)
            return
        try:
            overridden = {k: v for k in self.config.all()
                          if (v := self.config.overridden_by(k))}
            self.store.update("settings", values=self.config.all(),
                              overridden_by=overridden)
        except Exception:  # noqa: BLE001
            log.debug("settings section refresh failed", exc_info=True)

    def recheck_environment(self) -> None:
        tmux_ok, terminal_ok = self.sessions.check_environment()
        if not tmux_ok:
            self.store.toast("tmux still not found on PATH", error=True)
        elif not terminal_ok:
            self.store.toast(f"{terminal_display_name(self.sessions.terminal())} still "
                             "not found on PATH", error=True)
        else:
            self.store.toast("tmux and the terminal are available")
        self.sessions.refresh()

    def quit(self) -> None:
        self.store.emit(Event("quit", {}))
