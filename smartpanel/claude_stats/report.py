"""Plain-text gratification report — `python -m smartpanel --praise-report`.

The panel shows the running numbers; this is the sit-down version you can read
in a terminal (and on a headless box, since it never imports GTK). Pure string
building over a `Snapshot`, so it is unit-testable without a display server.
"""
from __future__ import annotations

from smartpanel.claude_stats.aggregator import Snapshot
from smartpanel.claude_stats.flattery import CHARS_PER_TOKEN
from smartpanel.render import humanize_tokens


def praise_report(s: Snapshot, top_n: int = 8) -> str:
    lines: list[str] = []
    add = lines.append

    add("Gratification report — what the compliments cost")
    add("=" * 56)
    add("")
    add(f"{'':10}{'phrases':>9}{'tokens':>10}{'written':>11}"
        f"{'echoed':>11}{'total':>11}{'of spend':>11}")
    for name, b in (("today", s.today), ("week", s.week), ("lifetime", s.lifetime)):
        add(f"{name:10}{b.praise_phrases:>9}{humanize_tokens(b.praise_tokens):>10}"
            f"{_usd(b.praise_cost_usd):>11}{_usd(b.praise_echo_cost_usd):>11}"
            f"{_usd(b.praise_total_cost):>11}{b.praise_cost_share:>10.2f}%")
    add("")
    life = s.lifetime
    add(f"Lifetime spend {_usd(life.cost_usd)} · output tokens "
        f"{humanize_tokens(life.tokens_output)} of which "
        f"{life.praise_share:.2f}% is gratification.")
    add(f"Dropping every compliment would have saved about "
        f"{_usd(life.praise_total_cost)}.")
    add("")

    add("Most expensive habits (lifetime)")
    add("-" * 56)
    if life.praise_by_label:
        ranked = sorted(life.praise_by_label.items(), key=lambda kv: (-kv[1], kv[0]))
        for label, n in ranked[:top_n]:
            add(f"  {label:<18}{n:>6}")
    else:
        add("  none found")
    add("")

    add("Top projects by gratification cost (lifetime)")
    add("-" * 56)
    ranked_p = sorted(s.per_project_lifetime.items(),
                      key=lambda kv: -kv[1].praise_total_cost)
    shown = [(n, b) for n, b in ranked_p if b.praise_phrases][:top_n]
    if shown:
        for name, b in shown:
            add(f"  {name[:22]:<22}{b.praise_phrases:>6} phrases"
                f"{_usd(b.praise_total_cost):>10}"
                f"{b.praise_cost_share:>9.2f}% of its spend")
    else:
        add("  none found")
    add("")

    add("How this is measured")
    add("-" * 56)
    add("  Transcripts record output tokens per message, never per sentence, so")
    add(f"  flagged text is sized at ~{CHARS_PER_TOKEN:.0f} chars/token. 'written' prices those")
    add("  tokens at the model's output rate; 'echoed' prices re-reading them as")
    add("  prompt on every later turn of the same session, at the cache-read rate.")
    add("  Code blocks and thinking blocks are excluded; only assistant prose is")
    add("  scanned. Treat the totals as an order of magnitude, not a bill.")
    return "\n".join(lines)


def _usd(amount: float) -> str:
    if amount and abs(amount) < 0.01:
        return f"${amount:.4f}"
    return f"${amount:,.2f}"
