"""Pure presentation for the Spend and Journal pages (no display needed)."""
import os
import time
from datetime import datetime

from smartpanel.demo import build_state
from smartpanel.state.model import (OUTCOME_JOURNALED, OUTCOME_TIMEOUT, DaySpend,
                                    JournalEntry, ModelSpend, ProjectSpend)
from smartpanel.ui import tokens
from smartpanel.ui.widgets import spend_journal_css as sjcss
from smartpanel.ui.widgets import spend_journal_model as m


def _day(**kw):
    base = dict(date="2026-11-17", label="Today", cost_usd=23.84, tokens=41_200_000,
                projects=[ProjectSpend("smartPanel", 9.20, 1), ProjectSpend("ledgerly", 2.09, 1),
                          ProjectSpend("idle", 0.0, 0)],
                models=[ModelSpend("Opus", 19.60, 1), ModelSpend("Sonnet", 3.91, 1),
                        ModelSpend("Haiku", 0.33, 1)],
                praise_cost_usd=0.07, praise_echo_cost_usd=0.21)
    base.update(kw)
    return DaySpend(**base)


# ── numbers ──

def test_money_and_amounts():
    assert m.money(None) == "—"
    assert m.money(2318.55) == "$2,318.55"
    assert m.amount(16.16) == "16.16"
    assert m.percent(94.4) == "94%" and m.percent(None) == "—"
    assert m.human_tokens(41_200_000) == "41.2M"
    assert m.human_tokens(950) == "950" and m.human_tokens(12_300) == "12.3k"


# ── receipt ──

def test_receipt_lines_today():
    printed = datetime(2026, 11, 17, 14, 32).timestamp()
    r = m.receipt_model(_day(), budget=40.0, printed_at=printed)
    assert r.date_line == "TUE 17 NOV 2026 · 14:32"
    assert r.projects == (("smartPanel", "9.20"), ("ledgerly", "2.09"))   # zero project hidden
    assert [n for n, _ in r.models] == ["Opus", "Sonnet", "Haiku"]
    assert r.total == "23.84"
    assert (r.praise, r.echo) == ("0.07", "0.21")
    assert r.budget == "40.00" and r.budget_rest == ("left", "16.16") and not r.over
    assert r.footer == "41.2M TOKENS · THANK YOU"
    assert not r.empty and r.has_praise


def test_receipt_past_day_has_no_time():
    r = m.receipt_model(_day(date="2026-11-16", label="Yesterday"), printed_at=time.time())
    assert r.date_line == "MON 16 NOV 2026"


def test_receipt_over_budget():
    r = m.receipt_model(_day(cost_usd=46.50), budget=40.0)
    assert r.budget_rest == ("over by", "6.50") and r.over


def test_receipt_hides_budget_and_praise_when_absent():
    r = m.receipt_model(_day(praise_cost_usd=0.0, praise_echo_cost_usd=0.001), budget=None)
    assert r.praise is None and r.echo is None and not r.has_praise
    assert r.budget is None and r.budget_rest is None


def test_receipt_echo_without_emit_still_shows_parent_line():
    r = m.receipt_model(_day(praise_cost_usd=0.0, praise_echo_cost_usd=0.05))
    assert r.praise == "0.00" and r.echo == "0.05"


def test_zero_day_receipt():
    r = m.receipt_model(_day(date="2026-11-14", label="Sat 14 Nov", cost_usd=0.0, tokens=0,
                             projects=[], models=[], praise_cost_usd=0, praise_echo_cost_usd=0),
                        budget=40.0)
    assert r.empty and r.date_line == "SAT 14 NOV 2026"
    assert r.budget is None and r.footer == ""


def test_budget_for_prefers_forecast_then_setting():
    st = build_state("busy")
    assert m.budget_for(st) == st.spend.forecast.budget_usd
    st.spend.forecast.budget_usd = None
    st.settings.values["budget.daily_usd"] = 25
    assert m.budget_for(st) == 25.0
    st.settings.values["budget.daily_usd"] = None
    assert m.budget_for(st) is None


def test_estimate_copy_is_the_backlog_wording():
    assert m.ESTIMATE_NOTE == ("Estimated from the text length. Transcripts record tokens "
                               "per message, not per sentence.")


# ── day list ──

def test_day_rows_scale_to_biggest_day():
    days = build_state("busy").spend.days
    rows = m.day_rows(days)
    assert len(rows) == len(days) == 14
    assert max(r.fraction for r in rows) == 1.0
    assert rows[0].label == "Today" and rows[0].sub        # "Tue 17 Nov" next to Today
    zero = [r for r, d in zip(rows, days) if d.cost_usd == 0]
    assert zero and all(r.fraction == 0 and r.total == "0.00" for r in zero)


def test_day_rows_all_zero_no_division():
    rows = m.day_rows([_day(cost_usd=0.0), _day(date="2026-11-16", label="Yesterday",
                                               cost_usd=0.0)])
    assert [r.fraction for r in rows] == [0.0, 0.0]


def test_pick_day_keeps_date_or_falls_back_to_today():
    days = build_state("busy").spend.days
    assert m.pick_day(days, None) == 0
    assert m.pick_day(days, days[3].date) == 3
    assert m.pick_day(days, "1999-01-01") == 0


# ── loading ──

def test_loading_copy():
    assert m.loading_text(812, 1284) == ("812 of 1,284 session files in ~/.claude/projects. "
                                         "Later refreshes only re-read files that changed.")
    assert m.loading_text(0, 0).startswith("Looking for session files")
    assert m.loading_fraction(642, 1284) == 0.5
    assert m.loading_fraction(5, 0) == 0.0


# ── journal ──

def _entry(**kw):
    base = dict(id="2026-11-17-1202-tacomap",
                path="~/.claude/smartpanel/journals/2026-11-17-1202-tacomap.md",
                time=datetime(2026, 11, 17, 12, 2).timestamp(), title="Map clustering",
                project="tacomap", outcome=OUTCOME_JOURNALED,
                confirmation="Journaled tacomap to JOURNAL/2026-11.md.", duration_s=9000,
                files=["a.ts", "b.ts"], commands=["npm test"],
                tools={"Edit": 9, "Read": 14})
    base.update(kw)
    return JournalEntry(**base)


def test_duration_format():
    assert m.duration(9000) == "2h30m"
    assert m.duration(3600 + 24 * 60) == "1h24m"
    assert m.duration(1500) == "25m"
    assert m.duration(40) == "40s"
    assert m.duration(None) == "—"


def test_journal_row():
    r = m.journal_row(_entry())
    assert (r.title, r.badge, r.badge_css) == ("Map clustering", "✓ JOURNALED", "ok")
    assert r.meta == "12:02 · tacomap · 2h30m"
    failed = m.journal_row(_entry(outcome=OUTCOME_TIMEOUT))
    assert (failed.badge, failed.badge_css) == ("RECEIPT ONLY", "fail")


def test_unreadable_entry():
    e = _entry(readable=False, title="", outcome="unknown")
    r = m.journal_row(e)
    assert r.title == "Couldn’t read this receipt" and r.badge == "UNREADABLE"
    d = m.journal_detail(e, e.time)
    assert d.failed and not d.readable and d.activity == ()
    assert d.receipt_file.endswith("2026-11-17-1202-tacomap.md")


def test_journal_detail():
    e = _entry()
    d = m.journal_detail(e, e.time + 3600, "~/dev/omdev/JOURNAL/2026-11.md")
    assert d.cap == "tacomap · today 12:02"
    assert d.quote == e.confirmation and not d.failed
    act = dict(d.activity)
    assert act["Duration"] == "2h30m"
    assert act["Files"] == "a.ts\nb.ts"
    assert act["Tools"] == "Read 14 · Edit 9"
    assert d.path.startswith("~/.claude/smartpanel/journals/")
    assert d.receipt_file == os.path.expanduser(e.path)
    assert d.log_name == "JOURNAL/2026-11.md"
    assert d.log_file == os.path.expanduser("~/dev/omdev/JOURNAL/2026-11.md")
    yesterday = m.journal_detail(e, e.time + 86400)
    assert yesterday.cap == "tacomap · yesterday 12:02" and yesterday.log_name is None


def test_journal_detail_failed_and_empty_lists():
    d = m.journal_detail(_entry(outcome=OUTCOME_TIMEOUT, confirmation="", files=[],
                                commands=[], tools={}), 0)
    assert d.failed and d.quote == "No confirmation line was recorded."
    assert dict(d.activity)["Files"] == "None" and dict(d.activity)["Tools"] == "None"


def test_journal_empty_copy_uses_min_age():
    assert m.journal_empty_text(120) == ("Close a session that has been open for more than "
                                         "2 minutes and smartPanel writes one here.")
    assert "1 minute and" in m.journal_empty_text(60)
    assert "90 seconds" in m.journal_empty_text(90)
    assert m.journal_empty_text(0) == "Close a session and smartPanel writes one here."
    assert "Settings" in m.journal_empty_text(120, journal_on=False)


def test_setting_reads_store_then_config():
    st = build_state("busy")
    st.settings.values["sessions.min_age_s"] = 300

    class Cfg:
        def get(self, key):
            return {"sessions.min_age_s": 60, "other": 1}.get(key)

    assert m.setting(st, Cfg(), "sessions.min_age_s") == 300
    del st.settings.values["sessions.min_age_s"]
    assert m.setting(st, Cfg(), "sessions.min_age_s") == 60
    assert m.setting(st, None, "missing", 7) == 7


# ── css ──

def test_receipt_ink_is_readable_on_paper():
    for ink in (sjcss.INK, sjcss.INK_MUTED, sjcss.INK_TIP):
        assert tokens.contrast(ink, sjcss.PAPER) >= 4.5, ink


def test_css_has_no_unfilled_placeholders():
    for dark in (True, False):
        assert "@@" not in sjcss.css(dark)
