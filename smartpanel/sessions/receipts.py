"""Index of journal receipts for the Journal page (SP-1802).

Receipts live in `~/.claude/smartpanel/journals/<YYYYmmdd-HHMMSS>-<project>.md`
(`journal.JOURNAL_DIR`, injectable). New ones open with YAML front matter
(SP-1801, written by `journal.run_journal`); older ones are plain Markdown and
are parsed best-effort from their headings. Anything that can't be read at all
is still listed, as `readable=False`, so a receipt never silently disappears.

Pure apart from the directory read; `ReceiptIndex` caches each file on
`(mtime, size)` so a rescan after a close only parses the new receipt.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from smartpanel.state.model import (
    OUTCOME_JOURNALED,
    OUTCOME_SKILL_FAILED,
    OUTCOME_TIMEOUT,
    OUTCOME_UNKNOWN,
    JournalEntry,
)

log = logging.getLogger(__name__)

_OUTCOMES = {OUTCOME_JOURNALED, OUTCOME_SKILL_FAILED, OUTCOME_TIMEOUT}
_STAMP = re.compile(r"^(\d{8}-\d{6})")
_HEADING = re.compile(r"^# Session journal — (.+)$", re.MULTILINE)
_WHEN = re.compile(r"^- \*\*When:\*\* (.+)$", re.MULTILINE)
_DURATION = re.compile(r"^- \*\*Duration:\*\* (.+)$", re.MULTILINE)
_SESSION = re.compile(r"^- \*\*Session:\*\* `(.+)`$", re.MULTILINE)
_TOOLS = re.compile(r"^- Tools: (.+)$", re.MULTILINE)
_TOOL_COUNT = re.compile(r"(\S+)×(\d+)")
_DURATION_PART = re.compile(r"(\d+)\s*([hms])")


def default_dir() -> Path:
    from smartpanel.sessions import journal
    return journal.JOURNAL_DIR


class ReceiptIndex:
    def __init__(self, directory: Path | str | None = None):
        self._directory = Path(directory) if directory else None
        self._cache: dict[str, tuple[float, int, JournalEntry]] = {}
        self._lock = threading.Lock()  # scans run on worker threads; two may overlap

    @property
    def directory(self) -> Path:
        return self._directory or default_dir()

    def scan(self) -> list[JournalEntry]:
        """Every receipt, newest first. Never raises."""
        with self._lock:
            return self._scan()

    def _scan(self) -> list[JournalEntry]:
        try:
            paths = [p for p in self.directory.glob("*.md") if p.is_file()]
        except OSError as e:
            log.warning("cannot list receipts in %s: %s", self.directory, e)
            return []
        seen: set[str] = set()
        entries: list[JournalEntry] = []
        for path in paths:
            key = str(path)
            seen.add(key)
            try:
                st = path.stat()
            except OSError:
                continue
            hit = self._cache.get(key)
            if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
                entries.append(hit[2])
                continue
            entry = load_receipt(path, mtime=st.st_mtime)
            self._cache[key] = (st.st_mtime, st.st_size, entry)
            entries.append(entry)
        for gone in set(self._cache) - seen:
            del self._cache[gone]
        entries.sort(key=lambda e: (e.time, e.id), reverse=True)
        return entries


def load_receipt(path: Path, mtime: float | None = None) -> JournalEntry:
    fallback_time = _time_from_name(path.stem)
    if fallback_time is None:
        try:
            fallback_time = mtime if mtime is not None else path.stat().st_mtime
        except OSError:
            fallback_time = 0.0
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        log.warning("cannot read receipt %s: %s", path, e)
        return _unreadable(path, fallback_time)
    entry = parse_receipt(text, path.stem, str(path), fallback_time)
    return entry if entry is not None else _unreadable(path, fallback_time)


def parse_receipt(text: str, id: str, path: str, fallback_time: float = 0.0) -> JournalEntry | None:
    """A receipt's text -> JournalEntry, or None if it isn't one we can read."""
    meta: dict = {}
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end < 0:
            return None
        try:
            import yaml
            meta = yaml.safe_load(text[4:end]) or {}
        except Exception:  # noqa: BLE001 — yaml.YAMLError and friends
            return None
        if not isinstance(meta, dict):
            return None
        body = text[end + 5:]
    elif not _HEADING.search(text):
        return None
    try:
        return _entry(meta, body, id, path, fallback_time)
    except Exception as e:  # noqa: BLE001 — garbage in a field: list it as unreadable
        log.warning("cannot parse receipt %s: %s", path, e)
        return None


def _entry(meta: dict, body: str, id: str, path: str, fallback_time: float) -> JournalEntry:
    heading = _HEADING.search(body)
    project = str(meta.get("project") or (heading.group(1).strip() if heading else ""))
    confirmation, body_outcome = _skill_section(body)
    outcome = str(meta.get("outcome") or body_outcome)
    if outcome not in _OUTCOMES:
        outcome = OUTCOME_UNKNOWN
    when = _parse_time(meta.get("time")) or _parse_time(_first(_WHEN, body)) or fallback_time
    duration = meta.get("duration_s") if "duration_s" in meta else _parse_duration(
        _first(_DURATION, body))
    files = meta.get("files") if "files" in meta else _bullets(body, "**Files touched:**")
    commands = meta.get("commands") if "commands" in meta else _bullets(body, "**Commands run:**")
    tools = meta.get("tools") if "tools" in meta else {
        k: int(v) for k, v in _TOOL_COUNT.findall(_first(_TOOLS, body) or "")}
    return JournalEntry(
        id=id,
        path=path,
        time=float(when),
        title=str(meta.get("title") or project),
        project=project,
        outcome=outcome,
        confirmation=str(meta.get("confirmation") or confirmation),
        duration_s=int(duration) if duration else None,
        files=[str(f) for f in (files or [])],
        commands=[str(c) for c in (commands or [])],
        tools={str(k): int(v) for k, v in (tools or {}).items()},
        readable=True,
    )


def _unreadable(path: Path, when: float) -> JournalEntry:
    return JournalEntry(id=path.stem, path=str(path), time=when, readable=False,
                        confirmation="Couldn't read this receipt")


def _skill_section(body: str) -> tuple[str, str]:
    """(confirmation line, outcome) from the "## `/journal`" section."""
    lines = body.splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith("## `")), None)
    if start is None:
        return "", OUTCOME_UNKNOWN
    chunk: list[str] = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        chunk.append(line)
    text = "\n".join(chunk).strip()
    if text.startswith("_(") and text.endswith(")_"):
        reason = text[2:-2].replace("`", "")
        if "had not finished" in reason:
            return reason, OUTCOME_TIMEOUT
        return reason, OUTCOME_SKILL_FAILED
    return text, OUTCOME_JOURNALED if text else OUTCOME_UNKNOWN


def _bullets(body: str, header: str) -> list[str]:
    lines = body.splitlines()
    try:
        i = lines.index(header)
    except ValueError:
        return []
    out: list[str] = []
    for line in lines[i + 1:]:
        if not line.startswith("- "):
            break
        item = line[2:].strip()
        if item.startswith("`") and item.endswith("`"):
            out.append(item[1:-1])
    return out


def _first(rx: re.Pattern, text: str) -> str | None:
    m = rx.search(text)
    return m.group(1).strip() if m else None


def _parse_time(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    for fmt in (None, "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.fromisoformat(str(value)) if fmt is None else \
                datetime.strptime(str(value), fmt)
            return dt.timestamp()
        except ValueError:
            continue
    return None


def _time_from_name(stem: str) -> float | None:
    m = _STAMP.match(stem)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").timestamp()
    except ValueError:
        return None


def _parse_duration(text: str | None) -> int | None:
    """'1h 2m' / '8m 24s' / '45s' -> seconds; 'unknown' -> None."""
    if not text:
        return None
    parts = _DURATION_PART.findall(text)
    if not parts:
        return None
    mult = {"h": 3600, "m": 60, "s": 1}
    return sum(int(n) * mult[u] for n, u in parts) or None


def monthly_log_path(now: datetime | None = None) -> str | None:
    """This month's workspace journal, if it exists (the `/journal` skill owns it).

    `SMARTPANEL_JOURNAL_LOG_DIR` points elsewhere; default `~/dev/omdev/JOURNAL`.
    """
    now = now or datetime.now()
    base = Path(os.environ.get("SMARTPANEL_JOURNAL_LOG_DIR")
                or Path.home() / "dev" / "omdev" / "JOURNAL").expanduser()
    p = base / f"{now:%Y-%m}.md"
    return str(p) if p.is_file() else None
