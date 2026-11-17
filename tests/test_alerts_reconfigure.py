"""Live threshold changes (SP-0103): `make_thresholds` + `AlertTracker.reconfigure`."""
from smartpanel.alerts import AlertTracker, Level, Thresholds, make_thresholds

T = Thresholds(warning=60, critical=85, hysteresis=5, repeat_s=600)


def test_make_thresholds_coerces_and_clamps():
    t = make_thresholds("70", 50, -3, "oops")
    assert t.warning == 70 and t.critical == 70      # crit below warn: clamped up
    assert t.hysteresis == 0                         # negative floored
    assert t.repeat_s == 600                         # garbage keeps the default
    d = make_thresholds(None, None, None, None)
    assert d == Thresholds()
    assert make_thresholds(True, 90).warning == 60   # a bool is not a percentage


def test_raise_warn_inside_band_holds_level_quietly():
    tr = AlertTracker(T)
    assert tr.update(66, now=0).level is Level.WARNING
    assert tr.reconfigure(make_thresholds(70, 85, 5)) is Level.WARNING  # 66 >= 70-5
    assert tr.update(66, now=10) is None
    assert tr.alerted


def test_raise_warn_above_band_drops_quietly_and_never_says_recovered():
    tr = AlertTracker(T)
    tr.update(66, now=0)
    assert tr.reconfigure(make_thresholds(75, 90, 5)) is Level.OK
    assert not tr.alerted
    assert tr.update(66, now=10) is None
    assert tr.update(20, now=20) is None             # no "back to normal" later


def test_reconfigure_keeps_critical_and_does_not_refire():
    tr = AlertTracker(T)
    tr.update(90, now=0)
    assert tr.reconfigure(make_thresholds(60, 88, 5)) is Level.CRITICAL
    assert tr.update(90, now=60) is None             # repeat clock untouched
    assert tr.update(90, now=600).level is Level.CRITICAL


def test_reconfigure_critical_to_warning_is_quiet_then_recovery_still_reported():
    tr = AlertTracker(T)
    tr.update(90, now=0)
    assert tr.reconfigure(make_thresholds(60, 97, 5)) is Level.WARNING
    assert tr.update(90, now=10) is None
    ev = tr.update(10, now=20)
    assert ev is not None and ev.level is Level.OK


def test_lowering_warn_escalates_on_the_next_reading():
    tr = AlertTracker(T)
    tr.update(40, now=0)
    assert tr.reconfigure(make_thresholds(30, 85, 5)) is Level.OK   # no bubble yet
    ev = tr.update(40, now=3)
    assert ev is not None and ev.level is Level.WARNING
