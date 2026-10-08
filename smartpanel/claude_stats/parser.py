from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from smartpanel.claude_stats import flattery

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UsageEvent:
    project: str            # display name, e.g. "webapp"
    timestamp: datetime     # UTC, falls back to file mtime if message has no timestamp
    model: str              # e.g. "claude-opus-4-7", empty string if unknown
    input_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    output_tokens: int
    message_id: str = ""            # dedupe key — see load_usage_events()
    praise_phrases: int = 0         # gratification matches in this message's prose
    praise_tokens: int = 0          # estimated output tokens spent on them
    praise_by_label: tuple[tuple[str, int], ...] = ()
    later_requests: int = 0         # requests after this one in the same session

    @property
    def total_tokens(self) -> int:
        return (self.input_tokens + self.cache_write_tokens
                + self.cache_read_tokens + self.output_tokens)


def project_display_name(encoded_path: str) -> str:
    """Decode a Claude projects directory name into a project display name.

    Claude Code encodes filesystem paths as `-` separated. We take the last
    non-empty segment as the display name. This is intentionally lossy —
    `/home/user/dev/X` and `/var/X` both yield `X`. Documented limitation.
    """
    parts = [p for p in encoded_path.split("-") if p]
    return parts[-1] if parts else encoded_path


def iter_usage_events(jsonl_path: Path, project: str,
                      fallback_ts: datetime | None = None) -> Iterator[UsageEvent]:
    """Stream usage events from a single .jsonl transcript file.

    Lines without a `message.usage` block are skipped. Malformed JSON lines
    are logged at DEBUG and skipped — transcripts can be appended to mid-write.
    """
    fallback_ts = fallback_ts or datetime.now(timezone.utc)
    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    log.debug("skipping malformed line in %s", jsonl_path.name)
                    continue
                ev = _extract_usage(obj, project, fallback_ts)
                if ev is not None:
                    yield ev
    except FileNotFoundError:
        return
    except OSError as e:
        log.warning("cannot read %s: %s", jsonl_path, e)


def load_usage_events(jsonl_path: Path, project: str,
                      fallback_ts: datetime | None = None) -> list[UsageEvent]:
    """Read one transcript into a list of **one event per API request**.

    Two things `iter_usage_events` deliberately doesn't do, both of which need
    the whole file in hand:

    1. **Deduplication.** Claude Code writes one line *per content block*, and
       every one of those lines repeats the same `message.usage`. Summing them
       line-by-line roughly doubles every token and dollar figure. Lines are
       grouped by `message.id`; a line without an id is its own message (older
       transcripts, and the synthetic lines tests write).
    2. **`later_requests`.** Output tokens are paid once, then re-sent as
       prompt on every subsequent turn of the session — so the true cost of a
       sentence depends on how many requests follow it. That is the number of
       remaining messages in this file.

    Praise is accumulated across all lines of a message, since the `text` block
    that holds the prose is only one of them.
    """
    order: list[str] = []
    merged: dict[str, UsageEvent] = {}

    for i, ev in enumerate(iter_usage_events(jsonl_path, project, fallback_ts)):
        key = ev.message_id or f"\0line-{i}"
        prev = merged.get(key)
        if prev is None:
            order.append(key)
            merged[key] = ev
            continue
        # Same request, another content block: keep the usage, fold in the prose.
        merged[key] = replace(
            prev,
            praise_phrases=prev.praise_phrases + ev.praise_phrases,
            praise_tokens=prev.praise_tokens + ev.praise_tokens,
            praise_by_label=tuple(
                flattery.merge_labels([prev.praise_by_label, ev.praise_by_label]).items()
            ),
        )

    total = len(order)
    return [replace(merged[key], later_requests=total - 1 - i)
            for i, key in enumerate(order)]


def _extract_usage(obj: dict, project: str, fallback_ts: datetime) -> UsageEvent | None:
    # Anthropic API events are wrapped in `{"type":"assistant","message":{...}}`
    # in Claude Code transcripts. Also handle the case where `usage` lives at top level.
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
    usage = msg.get("usage") if isinstance(msg, dict) else None
    if not isinstance(usage, dict):
        return None
    model = str(msg.get("model", "")) if isinstance(msg, dict) else ""

    ts_raw = obj.get("timestamp") or (msg.get("timestamp") if isinstance(msg, dict) else None)
    ts = _parse_ts(ts_raw) if ts_raw else fallback_ts

    # Only the assistant's own prose is scanned — a user quoting "good idea"
    # back at it isn't the assistant spending tokens on flattery.
    is_assistant = msg.get("role", "assistant") == "assistant"
    praise = (flattery.scan(flattery.message_text(msg.get("content")))
              if is_assistant else flattery.PraiseScan())

    return UsageEvent(
        project=project,
        timestamp=ts,
        model=model,
        input_tokens=int(usage.get("input_tokens") or 0),
        cache_write_tokens=int(usage.get("cache_creation_input_tokens") or 0),
        cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        message_id=str(msg.get("id") or ""),
        praise_phrases=praise.phrases,
        praise_tokens=praise.tokens,
        praise_by_label=praise.by_label,
    )


def _parse_ts(value) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        s = value.rstrip("Z")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return datetime.now(timezone.utc)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)
