from __future__ import annotations

import time

from smartpanel.claude_stats.aggregator import Aggregator, Bucket, Snapshot
from smartpanel.render import humanize_tokens

# Module-level aggregator + cached snapshot.
_AGG = Aggregator()
_CACHE: dict[str, object] = {"snapshot": None, "ts": 0.0}
_REFRESH_INTERVAL_S = 15.0

# Unicode figure space U+2007 — equal width to a digit in most fonts.
# Useful for right-aligning money columns inside menu rows.
FIG = " "

BAR_W = 10
BAR_FILLED = "█"
BAR_EMPTY = "░"


def _snapshot() -> Snapshot:
    now = time.monotonic()
    if (_CACHE["snapshot"] is None
            or now - float(_CACHE["ts"]) >= _REFRESH_INTERVAL_S):
        _CACHE["snapshot"] = _AGG.refresh()
        _CACHE["ts"] = now
    return _CACHE["snapshot"]  # type: ignore[return-value]


def _money(amount: float, width: int = 9) -> str:
    """Right-align a dollar amount inside a column of figure spaces."""
    return f"${amount:,.2f}".rjust(width, FIG)


def _bar(value: float, total: float) -> str:
    if total <= 0:
        return BAR_EMPTY * BAR_W
    pct = max(0.0, min(1.0, value / total))
    filled = int(round(pct * BAR_W))
    return BAR_FILLED * filled + BAR_EMPTY * (BAR_W - filled)


def _format_projects(per_project: dict[str, Bucket], total_cost: float,
                     top_n: int = 10) -> str:
    if not per_project:
        return "—"
    ranked = sorted(per_project.items(), key=lambda kv: kv[1].cost_usd, reverse=True)
    rows = []
    name_w = min(20, max((len(n) for n, _ in ranked[:top_n]), default=0))
    for name, b in ranked[:top_n]:
        pct = (b.cost_usd / total_cost * 100.0) if total_cost else 0.0
        tokens = humanize_tokens(b.tokens_total).rjust(7, FIG)
        rows.append(
            f"{name.ljust(name_w)}  {_money(b.cost_usd)}  {tokens} tok  "
            f"{_bar(b.cost_usd, total_cost)}  {pct:>4.1f}%"
        )
    return " · ".join(rows)


def _format_models(per_model: dict[str, Bucket], total_cost: float) -> str:
    if not per_model:
        return "—"
    ranked = sorted(per_model.items(), key=lambda kv: kv[1].cost_usd, reverse=True)
    rows = []
    for name, b in ranked:
        pct = (b.cost_usd / total_cost * 100.0) if total_cost else 0.0
        rows.append(f"{name.ljust(8)}  {_money(b.cost_usd)}  ({pct:>4.1f}%)")
    return " · ".join(rows)


def _format_praise(s: Snapshot) -> str:
    """One row per horizon: phrases, tokens, and the direct + echo dollar split."""
    rows = []
    for name, b in (("today", s.today), ("week", s.week), ("lifetime", s.lifetime)):
        rows.append(
            f"{name.ljust(8)}  {str(b.praise_phrases).rjust(4, FIG)} phrases  "
            f"{humanize_tokens(b.praise_tokens).rjust(7, FIG)} tok  "
            f"{_money(b.praise_total_cost)}  "
            f"({b.praise_cost_share:.2f}% of spend)"
        )
    # NB: " · " is the row separator for MULTI_ROW_METRICS — never use it inside
    # a row, or the row splits in two on screen.
    rows.append(
        f"{'of which'.ljust(8)}  {_money(s.lifetime.praise_cost_usd, 0)} written, "
        f"{_money(s.lifetime.praise_echo_cost_usd, 0)} re-read in later turns"
    )
    return " · ".join(rows)


def _format_top_praise(by_label: dict[str, int], top_n: int = 6) -> str:
    if not by_label:
        return "—"
    ranked = sorted(by_label.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
    total = sum(by_label.values())
    name_w = max(len(n) for n, _ in ranked)
    return " · ".join(
        f'"{name}"'.ljust(name_w + 2)
        + f"  ×{str(n).rjust(4, FIG)}  {_bar(n, total)}"
        for name, n in ranked
    )


def claude_stats() -> dict[str, float | str | None]:
    s = _snapshot()

    return {
        # Headline scalars
        "COST_TODAY": s.today.cost_usd,
        "COST_WEEK": s.week.cost_usd,
        "COST_LIFETIME": s.lifetime.cost_usd,
        "TOKENS_TODAY": float(s.today.tokens_total),
        "TOKENS_TODAY_HUMAN": humanize_tokens(s.today.tokens_total),
        "TOKENS_WEEK": float(s.week.tokens_total),
        "TOKENS_LIFETIME": float(s.lifetime.tokens_total),
        "TOKENS_LIFETIME_HUMAN": humanize_tokens(s.lifetime.tokens_total),
        "CACHE_HIT_RATIO": s.today.cache_hit_ratio,
        "BURN_RATE": s.burn_rate_per_hour,
        "SESSIONS_TODAY": float(s.sessions_today),
        # Gratification — what the compliments cost (see claude_stats/flattery.py)
        "PRAISE_PHRASES_TODAY": float(s.today.praise_phrases),
        "PRAISE_PHRASES_LIFETIME": float(s.lifetime.praise_phrases),
        "PRAISE_TOKENS_TODAY": float(s.today.praise_tokens),
        "PRAISE_TOKENS_LIFETIME": float(s.lifetime.praise_tokens),
        "PRAISE_COST_TODAY": s.today.praise_total_cost,
        "PRAISE_COST_WEEK": s.week.praise_total_cost,
        "PRAISE_COST_LIFETIME": s.lifetime.praise_total_cost,
        "PRAISE_SHARE": s.lifetime.praise_share,
        # Multi-row blocks rendered specially by the indicator
        "PER_PROJECT": _format_projects(s.per_project_today, s.today.cost_usd),
        "BY_MODEL": _format_models(s.per_model_today, s.today.cost_usd),
        "PRAISE_SUMMARY": _format_praise(s),
        "TOP_PRAISE": _format_top_praise(s.lifetime.praise_by_label),
    }
