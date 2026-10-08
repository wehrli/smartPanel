from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from smartpanel.claude_stats.flattery import scan
from smartpanel.claude_stats.parser import (
    iter_usage_events,
    load_usage_events,
    project_display_name,
)


def _usage(**over) -> dict:
    u = {"input_tokens": 10, "cache_creation_input_tokens": 0,
         "cache_read_input_tokens": 0, "output_tokens": 100}
    u.update(over)
    return u


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(line) for line in lines))


def test_project_display_name_takes_last_segment():
    assert project_display_name("-home-user-projects-webapp") == "webapp"
    assert project_display_name("-home-user") == "user"
    assert project_display_name("") == ""


def test_extracts_usage_from_top_level_message(tmp_path: Path):
    f = tmp_path / "session.jsonl"
    _write_jsonl(f, [
        {"type": "permission-mode"},  # ignored
        {"message": {
            "model": "claude-opus-4-7",
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 1000,
                "output_tokens": 50,
            },
        }, "timestamp": "2026-05-25T10:00:00Z"},
    ])
    events = list(iter_usage_events(f, project="X"))
    assert len(events) == 1
    ev = events[0]
    assert ev.project == "X"
    assert ev.model == "claude-opus-4-7"
    assert ev.input_tokens == 10
    assert ev.cache_write_tokens == 100
    assert ev.cache_read_tokens == 1000
    assert ev.output_tokens == 50
    assert ev.timestamp.tzinfo is not None


def test_skips_malformed_lines(tmp_path: Path):
    f = tmp_path / "session.jsonl"
    f.write_text(
        '{"message":{"usage":{"input_tokens":1,"output_tokens":2}}}\n'
        "not-json\n"
        '{"message":{"usage":{"input_tokens":3,"output_tokens":4}}}\n'
    )
    events = list(iter_usage_events(f, project="X",
                                    fallback_ts=datetime.now(timezone.utc)))
    assert [e.input_tokens for e in events] == [1, 3]


def test_missing_file_returns_empty(tmp_path: Path):
    events = list(iter_usage_events(tmp_path / "nope.jsonl", project="X"))
    assert events == []


# --- load_usage_events: one event per request, not per content block ---

def test_content_block_lines_of_one_message_are_charged_once(tmp_path: Path):
    """Claude Code repeats `message.usage` on every content-block line."""
    f = tmp_path / "session.jsonl"
    _write_jsonl(f, [
        {"message": {"id": "msg_1", "role": "assistant", "usage": _usage(),
                     "content": [{"type": "thinking", "thinking": "..."}]}},
        {"message": {"id": "msg_1", "role": "assistant", "usage": _usage(),
                     "content": [{"type": "text", "text": "Here it is."}]}},
        {"message": {"id": "msg_1", "role": "assistant", "usage": _usage(),
                     "content": [{"type": "tool_use", "name": "Bash"}]}},
    ])
    assert len(list(iter_usage_events(f, "X"))) == 3   # raw lines
    events = load_usage_events(f, "X")
    assert len(events) == 1
    assert events[0].output_tokens == 100             # not 300


def test_lines_without_a_message_id_stay_distinct(tmp_path: Path):
    f = tmp_path / "session.jsonl"
    _write_jsonl(f, [
        {"message": {"usage": _usage(output_tokens=5)}},
        {"message": {"usage": _usage(output_tokens=7)}},
    ])
    assert [e.output_tokens for e in load_usage_events(f, "X")] == [5, 7]


def test_praise_is_merged_across_a_messages_blocks(tmp_path: Path):
    f = tmp_path / "session.jsonl"
    _write_jsonl(f, [
        {"message": {"id": "m1", "role": "assistant", "usage": _usage(),
                     "content": [{"type": "text", "text": "Good question."}]}},
        {"message": {"id": "m1", "role": "assistant", "usage": _usage(),
                     "content": [{"type": "text", "text": "You're right."}]}},
    ])
    ev = load_usage_events(f, "X")[0]
    assert ev.praise_phrases == 2
    assert ev.praise_tokens == scan("Good question.").tokens + scan("You're right.").tokens
    assert dict(ev.praise_by_label) == {"good question": 1, "you're right": 1}


def test_user_messages_are_not_scanned_for_praise(tmp_path: Path):
    f = tmp_path / "session.jsonl"
    _write_jsonl(f, [
        {"message": {"id": "m1", "role": "user", "usage": _usage(),
                     "content": [{"type": "text", "text": "Good question, right?"}]}},
    ])
    assert load_usage_events(f, "X")[0].praise_phrases == 0


def test_later_requests_counts_the_rest_of_the_session(tmp_path: Path):
    f = tmp_path / "session.jsonl"
    _write_jsonl(f, [
        {"message": {"id": f"m{i}", "role": "assistant", "usage": _usage()}}
        for i in range(4)
    ])
    assert [e.later_requests for e in load_usage_events(f, "X")] == [3, 2, 1, 0]
