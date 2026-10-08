from __future__ import annotations

from smartpanel.claude_stats.aggregator import Bucket, Snapshot
from smartpanel.claude_stats.report import praise_report


def _bucket(**over) -> Bucket:
    b = Bucket(tokens_input=1_000, tokens_output=10_000, cost_usd=10.0)
    for k, v in over.items():
        setattr(b, k, v)
    return b


def _snapshot() -> Snapshot:
    life = _bucket(praise_phrases=68, praise_tokens=1_000,
                   praise_cost_usd=0.08, praise_echo_cost_usd=0.11,
                   praise_by_label={"you're right": 25, "good question": 19})
    return Snapshot(
        today=_bucket(praise_phrases=7, praise_tokens=111, praise_cost_usd=0.008),
        week=_bucket(praise_phrases=11, praise_tokens=189, praise_cost_usd=0.01),
        lifetime=life,
        per_project_lifetime={
            "apiserver": _bucket(praise_phrases=27, praise_cost_usd=0.09),
            "quiet": _bucket(),  # no praise — must not be listed
        },
    )


def test_report_shows_every_horizon_and_the_headline_saving():
    out = praise_report(_snapshot())
    for horizon in ("today", "week", "lifetime"):
        assert horizon in out
    assert "$0.19" in out            # 0.08 written + 0.11 echoed
    assert "would have saved about $0.19" in out


def test_report_ranks_habits_and_projects_and_hides_clean_ones():
    out = praise_report(_snapshot())
    assert out.index("you're right") < out.index("good question")  # by count
    assert "apiserver" in out
    assert "quiet" not in out


def test_report_on_an_empty_snapshot_says_none_rather_than_crashing():
    out = praise_report(Snapshot())
    assert "none found" in out
    assert "$0.00" in out


def test_sub_cent_amounts_keep_their_precision():
    snap = Snapshot(lifetime=_bucket(praise_phrases=1, praise_cost_usd=0.0083))
    assert "$0.0083" in praise_report(snap)
