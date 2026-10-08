from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smartpanel.claude_stats.aggregator import Aggregator
from smartpanel.claude_stats.flattery import scan


FIXED_NOW = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)


def _make_project(root: Path, encoded: str, lines: list[dict]) -> Path:
    pdir = root / encoded
    pdir.mkdir(parents=True)
    f = pdir / "session.jsonl"
    f.write_text("\n".join(json.dumps(line) for line in lines))
    return f


def _msg(model: str, ts: datetime, input_t: int, output_t: int,
         cache_w: int = 0, cache_r: int = 0, text: str | None = None,
         msg_id: str | None = None) -> dict:
    message = {
        "model": model,
        "role": "assistant",
        "usage": {
            "input_tokens": input_t,
            "cache_creation_input_tokens": cache_w,
            "cache_read_input_tokens": cache_r,
            "output_tokens": output_t,
        },
    }
    if msg_id:
        message["id"] = msg_id
    if text is not None:
        message["content"] = [{"type": "text", "text": text}]
    return {
        "message": message,
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
    }


def test_aggregates_per_project_today_and_lifetime(tmp_path: Path):
    today = FIXED_NOW
    yesterday = FIXED_NOW - timedelta(days=1)
    last_month = FIXED_NOW - timedelta(days=30)

    _make_project(tmp_path, "-home-user-projects-webapp", [
        _msg("claude-sonnet-4-6", today, input_t=1000, output_t=500),
        _msg("claude-sonnet-4-6", yesterday, input_t=2000, output_t=1000),
    ])
    _make_project(tmp_path, "-home-user-projects-apiserver", [
        _msg("claude-haiku-4-5", today, input_t=10_000, output_t=5_000),
        _msg("claude-opus-4-7", last_month, input_t=500, output_t=200),
    ])

    agg = Aggregator(claude_root=tmp_path, now_fn=lambda: FIXED_NOW)
    snap = agg.refresh()

    assert snap.today.tokens_total == 1000 + 500 + 10_000 + 5_000
    assert snap.lifetime.tokens_total == 1000 + 500 + 2000 + 1000 + 10_000 + 5_000 + 500 + 200
    assert set(snap.per_project_today.keys()) == {"webapp", "apiserver"}
    assert snap.per_project_today["webapp"].tokens_total == 1500
    assert snap.per_project_today["apiserver"].tokens_total == 15_000


def test_costs_use_model_pricing(tmp_path: Path):
    _make_project(tmp_path, "-home-X", [
        # 1M input + 1M output on Opus
        _msg("claude-opus-4-7", FIXED_NOW, input_t=1_000_000, output_t=1_000_000),
    ])
    agg = Aggregator(claude_root=tmp_path, now_fn=lambda: FIXED_NOW)
    snap = agg.refresh()
    # Opus: $15/M in + $75/M out = $90
    assert abs(snap.today.cost_usd - 90.0) < 0.01


def test_mtime_cache_skips_unchanged_file(tmp_path: Path):
    f = _make_project(tmp_path, "-X", [
        _msg("claude-sonnet-4-6", FIXED_NOW, input_t=100, output_t=50),
    ])
    agg = Aggregator(claude_root=tmp_path, now_fn=lambda: FIXED_NOW)
    agg.refresh()
    # Tamper with the file but keep mtime: aggregator should use cache.
    mtime = f.stat().st_mtime_ns
    f.write_text("garbage")
    import os
    os.utime(f, ns=(mtime, mtime))
    snap2 = agg.refresh()
    assert snap2.today.tokens_total == 150  # cached, not re-parsed


def test_praise_is_counted_and_priced_at_output_rates(tmp_path: Path):
    praise = "Good question."
    _make_project(tmp_path, "-home-X", [
        _msg("claude-opus-4-7", FIXED_NOW, input_t=10, output_t=1000,
             text=f"{praise} The handler is fine.", msg_id="m1"),
    ])
    snap = Aggregator(claude_root=tmp_path, now_fn=lambda: FIXED_NOW).refresh()

    tokens = scan(praise).tokens
    assert snap.today.praise_phrases == 1
    assert snap.today.praise_tokens == tokens
    # Opus output is $75/M. Nothing follows in the session, so no echo cost.
    assert abs(snap.today.praise_cost_usd - tokens * 75.0 / 1e6) < 1e-9
    assert snap.today.praise_echo_cost_usd == 0.0
    assert snap.today.praise_by_label == {"good question": 1}


def test_praise_echo_is_priced_per_later_request(tmp_path: Path):
    praise = "Good question."
    _make_project(tmp_path, "-home-X", [
        _msg("claude-opus-4-7", FIXED_NOW, input_t=10, output_t=100,
             text=praise, msg_id="m1"),
        # Two more requests in the same session: the compliment is re-sent as
        # prompt on both, at the Opus cache-read rate of $1.50/M.
        _msg("claude-opus-4-7", FIXED_NOW, input_t=10, output_t=100, msg_id="m2"),
        _msg("claude-opus-4-7", FIXED_NOW, input_t=10, output_t=100, msg_id="m3"),
    ])
    snap = Aggregator(claude_root=tmp_path, now_fn=lambda: FIXED_NOW).refresh()

    tokens = scan(praise).tokens
    assert abs(snap.today.praise_echo_cost_usd - tokens * 2 * 1.50 / 1e6) < 1e-9
    assert (snap.today.praise_total_cost
            == snap.today.praise_cost_usd + snap.today.praise_echo_cost_usd)


def test_a_message_resumed_into_a_second_transcript_is_counted_once(tmp_path: Path):
    """Resuming copies the prior conversation into the new session file."""
    pdir = tmp_path / "-home-X"
    pdir.mkdir(parents=True)
    original = _msg("claude-opus-4-7", FIXED_NOW, input_t=1000, output_t=500,
                    text="Good question.", msg_id="shared")
    for name in ("first.jsonl", "second.jsonl"):
        (pdir / name).write_text(json.dumps(original))

    snap = Aggregator(claude_root=tmp_path, now_fn=lambda: FIXED_NOW).refresh()
    assert snap.today.tokens_total == 1500
    assert snap.today.praise_phrases == 1


def test_cache_hit_ratio(tmp_path: Path):
    _make_project(tmp_path, "-X", [
        _msg("claude-sonnet-4-6", FIXED_NOW, input_t=100, output_t=50,
             cache_w=0, cache_r=900),
    ])
    agg = Aggregator(claude_root=tmp_path, now_fn=lambda: FIXED_NOW)
    snap = agg.refresh()
    # cache_read / (cache_read + input + cache_write) = 900 / 1000
    assert abs(snap.today.cache_hit_ratio - 90.0) < 0.1
