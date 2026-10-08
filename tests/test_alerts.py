"""Alert policy tests — all pure, no display server, no psutil.

The three things worth pinning down: the badge string the top bar shows, the
hysteresis that stops it flickering, and *when* a notification is allowed to
fire. The tracker takes `now` as an argument precisely so the repeat timing can
be tested without sleeping.
"""
import pytest

from smartpanel.alerts import (
    AlertTracker,
    Level,
    Thresholds,
    next_level,
    raw_level,
    swap_badge,
    swap_detail,
    swap_status,
    thresholds_from_env,
)

T = Thresholds(warning=60, critical=85, hysteresis=5, repeat_s=600)


def feed(tracker, readings):
    """Run (percent, now) pairs through a tracker, collecting the events."""
    return [tracker.update(p, now=n) for p, n in readings]


# ---- levels ----

def test_raw_level_boundaries_are_inclusive():
    assert raw_level(59.9, T) is Level.OK
    assert raw_level(60.0, T) is Level.WARNING
    assert raw_level(84.9, T) is Level.WARNING
    assert raw_level(85.0, T) is Level.CRITICAL


def test_no_swap_configured_is_always_ok():
    assert raw_level(None, T) is Level.OK
    assert next_level(None, Level.CRITICAL, T) is Level.OK
    assert swap_badge(Level.CRITICAL, None) == ""


def test_escalation_is_immediate():
    assert next_level(86, Level.OK, T) is Level.CRITICAL


def test_hysteresis_holds_the_level_inside_the_band():
    # Critical at 85, band 5 wide: 80..84.9 stays CRITICAL despite raw WARNING.
    assert next_level(82, Level.CRITICAL, T) is Level.CRITICAL
    assert next_level(79.9, Level.CRITICAL, T) is Level.WARNING
    # Same rule one step down.
    assert next_level(57, Level.WARNING, T) is Level.WARNING
    assert next_level(54.9, Level.WARNING, T) is Level.OK


def test_hysteresis_does_not_trap_a_large_drop():
    assert next_level(3, Level.CRITICAL, T) is Level.OK


# ---- status gauge ----

def test_status_is_always_rendered_and_carries_the_severity_glyph():
    assert swap_status(Level.OK, 12.4) == "🔃12%"
    assert swap_status(Level.WARNING, 62.4) == "⚠🔃62%"
    assert swap_status(Level.CRITICAL, 91.6) == "🔴🔃92%"
    # Top bar budget is ~30 chars (see CLAUDE.md) — the gauge must stay tiny.
    assert len(swap_status(Level.CRITICAL, 100.0)) <= 8


def test_status_shows_a_dash_when_no_swap_is_configured():
    """Never a reassuring '0%' on a box that simply has no swap."""
    assert swap_status(Level.OK, None) == "🔃—"


def test_tracker_status_tracks_the_hysteresis_level():
    t = AlertTracker(T)
    t.update(90, now=0)
    assert t.status == "🔴🔃90%"
    t.update(82, now=1)  # inside the sticky band — still critical
    assert t.status == "🔴🔃82%"
    t.update(10, now=2)
    assert t.status == "🔃10%" and t.badge == ""


# ---- badge ----

def test_badge_is_empty_when_ok_and_compact_when_not():
    assert swap_badge(Level.OK, 12.0) == ""
    assert swap_badge(Level.WARNING, 62.4) == "⚠🔃62%"
    assert swap_badge(Level.CRITICAL, 91.6) == "🔴🔃92%"
    # Top bar budget is ~30 chars (see CLAUDE.md) — the badge must stay tiny.
    assert len(swap_badge(Level.CRITICAL, 100.0)) <= 8


# ---- notification policy ----

def test_notifies_once_on_entry_then_stays_quiet():
    t = AlertTracker(T)
    events = feed(t, [(10, 0), (65, 1), (70, 2), (80, 3)])
    assert [bool(e) for e in events] == [False, True, False, False]
    assert events[1].level is Level.WARNING
    assert events[1].urgency == "normal"


def test_escalation_to_critical_notifies_again_with_critical_urgency():
    t = AlertTracker(T)
    events = feed(t, [(65, 0), (90, 1)])
    assert events[1].level is Level.CRITICAL
    assert events[1].urgency == "critical"
    assert "thrashing" in events[1].body


def test_de_escalation_to_warning_is_silent():
    """Improving is not news. Only the badge changes."""
    t = AlertTracker(T)
    feed(t, [(90, 0)])
    assert t.update(78, now=1) is None
    assert t.level is Level.WARNING
    assert t.badge == "⚠🔃78%"


def test_recovery_notifies_once_and_only_after_an_alert():
    t = AlertTracker(T)
    feed(t, [(90, 0)])
    recovered = t.update(20, now=1)
    assert recovered is not None and recovered.level is Level.OK
    assert recovered.urgency == "low"
    assert t.badge == ""
    # And never twice.
    assert t.update(15, now=2) is None


def test_never_announces_recovery_without_a_prior_alert():
    t = AlertTracker(T)
    assert t.update(5, now=0) is None
    assert t.update(3, now=1) is None


def test_critical_repeats_only_after_the_repeat_interval():
    t = AlertTracker(Thresholds(warning=60, critical=85, repeat_s=100))
    assert t.update(90, now=0) is not None      # entry
    assert t.update(91, now=99) is None         # too soon
    assert t.update(92, now=100) is not None    # nag
    assert t.update(93, now=150) is None
    assert t.update(94, now=200) is not None


def test_repeat_disabled_by_zero():
    t = AlertTracker(Thresholds(warning=60, critical=85, repeat_s=0))
    assert t.update(90, now=0) is not None
    assert t.update(95, now=10_000) is None


def test_tracker_exposes_state_for_the_badge():
    t = AlertTracker(T)
    assert not t.active and t.badge == ""
    t.update(95, now=0)
    assert t.active and t.level is Level.CRITICAL and t.badge == "🔴🔃95%"


# ---- detail text ----

def test_detail_includes_absolute_sizes_when_known():
    text = swap_detail(Level.CRITICAL, 92.0, 7.4, 8.0)
    assert "92%" in text and "7.4 of 8.0 GiB" in text


def test_detail_survives_missing_sizes():
    assert "GiB" not in swap_detail(Level.WARNING, 62.0, None, None)
    assert swap_detail(Level.OK, None, None, None) == "No swap configured."


# ---- env configuration ----

def test_thresholds_from_env_reads_overrides():
    t = thresholds_from_env({"SMARTPANEL_SWAP_WARN": "40",
                             "SMARTPANEL_SWAP_CRIT": "70",
                             "SMARTPANEL_SWAP_REPEAT": "30"})
    assert (t.warning, t.critical, t.repeat_s) == (40.0, 70.0, 30.0)


def test_thresholds_from_env_ignores_garbage_instead_of_raising():
    """A typo in the environment must not take the panel down."""
    t = thresholds_from_env({"SMARTPANEL_SWAP_WARN": "sixty", "SMARTPANEL_SWAP_CRIT": ""})
    assert t.warning == Thresholds().warning
    assert t.critical == Thresholds().critical


def test_inverted_thresholds_are_clamped_not_honoured():
    t = thresholds_from_env({"SMARTPANEL_SWAP_WARN": "90", "SMARTPANEL_SWAP_CRIT": "50"})
    assert t.critical == t.warning == 90.0
    assert raw_level(95, t) is Level.CRITICAL


@pytest.mark.parametrize("percent,expected", [
    (0.0, Level.OK), (59.9, Level.OK), (60.0, Level.WARNING), (100.0, Level.CRITICAL),
])
def test_fresh_tracker_matches_raw_level(percent, expected):
    t = AlertTracker(T)
    t.update(percent, now=0)
    assert t.level is expected
