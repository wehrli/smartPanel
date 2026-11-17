"""SystemCollector: sensor dicts -> `system` state, swap alert policy, live settings.

No GLib timers, no real notifications: readers are the real sensor-shaped dicts,
the notifier records calls, and the clock is a variable. `start()`/`stop()` are
exercised once against the real GLib main context without running the loop.
"""
import pytest

from smartpanel import alerts
from smartpanel.collect import system as collect
from smartpanel.collect.system import SystemCollector, export_points, swap_notification
from smartpanel.config import Config
from smartpanel.state.model import LEVEL_CRITICAL, LEVEL_OK, LEVEL_WARNING
from smartpanel.state.store import Store
from smartpanel.theme.model import MetricRender, Theme

GIB = 1.0


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, kind, title, body, *, action=None):
        self.calls.append((kind, title, body, action))
        return True

    @property
    def kinds(self):
        return [c[0] for c in self.calls]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("SMARTPANEL_SWAP_WARN", "SMARTPANEL_SWAP_CRIT",
              "SMARTPANEL_SWAP_HYSTERESIS", "SMARTPANEL_SWAP_REPEAT"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def cfg(tmp_path):
    return Config.load(tmp_path / "config.yaml")


class Closer:
    def __init__(self):
        self.keys = []

    def __call__(self, key):
        self.keys.append(key)
        return True


def make(cfg, theme=None, closer=None, **readers):
    store, rec, clock = Store(), Recorder(), Clock()
    col = SystemCollector(store, cfg, theme, readers=readers, notifier=rec,
                          closer=closer or Closer(), clock=clock)
    return col, store, rec, clock


def swap_reading(pct, total=8.0):
    if pct is None:
        return {"PERCENTAGE": None, "USED": 0.0, "FREE": 0.0, "TOTAL": 0.0}
    return {"PERCENTAGE": float(pct), "USED": total * pct / 100,
            "FREE": total * (1 - pct / 100), "TOTAL": total}


# ── SP-0112: the prototype's "Swap climbs" then "Swap recovers" flows ──

def prototype_path(start, target):
    """Same stepping as the prototype's swapAnim: big steps, then land on target."""
    out, cur = [], start
    while abs(target - cur) >= 2:
        d = target - cur
        cur = round(cur + (1 if d > 0 else -1) * max(2, abs(d) / 6))
        out.append(cur)
    out.append(target)
    return out


# Hand-written oracle for the default thresholds (warn 60, crit 85, hysteresis 5):
# OK below 60, WARNING from 60, CRITICAL from 85; on the way down CRITICAL holds
# until below 80 and WARNING until below 55. Deliberately not alerts.next_level.
O, W, C = LEVEL_OK, LEVEL_WARNING, LEVEL_CRITICAL
JOURNEY = [
    (26, O), (37, O), (46, O), (54, O), (60, W), (66, W), (70, W), (74, W),
    (77, W), (80, W), (82, W), (84, W), (86, C), (88, C), (90, C), (92, C),
    (93, C),
    (80, C), (69, W), (60, W), (52, O), (46, O), (41, O), (37, O), (33, O),
    (30, O), (28, O), (26, O), (24, O), (22, O), (20, O), (18, O), (16, O),
    (15, O),
]


def test_journey_table_is_the_prototype_path():
    assert [p for p, _ in JOURNEY] == prototype_path(12, 93) + prototype_path(93, 15)


def test_swap_journey_12_93_15(cfg):
    # SP-0112, collector half. The bar-text half (SP-0104's "12" / "⚠66" / "⚠88"
    # A1 segment) belongs to WP-tray's barspec and is asserted at integration.
    col, store, rec, clock = make(cfg)
    col.apply_swap(swap_reading(12))
    assert rec.calls == []
    assert store.state.system.swap.level == LEVEL_OK

    seen = []
    for p, expected in JOURNEY:
        clock.t += 3
        col.apply_swap(swap_reading(p))
        s = store.state.system.swap
        seen.append((p, s.level))
        assert s.level == expected, (p, s.level, expected)
        assert s.percent == p

    assert rec.kinds == ["swap_warning", "swap_critical", "swap_ok"]
    warn, crit, ok = rec.calls
    assert warn[1] == "Swap is filling up"
    assert crit[1] == "Swap critically full" and crit[2].startswith("86% used (6.9 of 8 GB). ")
    assert ok[1] == "Swap back to normal" and ok[2] == "52% used. The alert is cleared."  # first reading below 60-5
    assert warn[3] is not None and warn[3].label == "Open System"
    assert ok[3] is None
    # Went through critical -> warning on the way down without a bubble.
    levels = [lv for _, lv in seen]
    i_crit = levels.index(LEVEL_CRITICAL)
    assert LEVEL_WARNING in levels[i_crit:]
    assert store.state.system.swap.level == LEVEL_OK
    assert store.state.system.swap.alerted is False
    assert store.state.system.swap.history[-1] == 15.0


def test_critical_repeats_every_repeat_s(cfg):
    col, store, rec, clock = make(cfg)
    col.apply_swap(swap_reading(90))
    for _ in range(59):              # 590 s of 10 s polls: quiet
        clock.t += 10
        col.apply_swap(swap_reading(90))
    assert rec.kinds == ["swap_critical"]
    clock.t += 10
    col.apply_swap(swap_reading(90))
    assert rec.kinds == ["swap_critical", "swap_critical"]
    assert store.state.system.swap.alerted is True


def test_open_system_action_emits_dashboard_event(cfg):
    col, store, rec, _ = make(cfg)
    events = []
    store.on_event(events.append)
    col.apply_swap(swap_reading(70))
    rec.calls[0][3].callback()
    assert events[0].kind == "open_dashboard" and events[0].data == {"page": "system"}


def test_no_swap_is_not_zero_and_never_alerts(cfg):
    col, store, rec, _ = make(cfg)
    for _ in range(3):
        col.apply_swap(swap_reading(None))
    s = store.state.system.swap
    assert s.percent is None and s.used_gb is None and s.total_gb is None
    assert s.level == LEVEL_OK and s.history == []     # SP-0102: nothing pushed
    assert rec.calls == []


def test_swap_state_carries_thresholds(cfg):
    col, store, _, _ = make(cfg)
    s = store.state.system.swap
    assert (s.warn_at, s.crit_at) == (60.0, 85.0)


# ── SP-0103: thresholds from settings, applied live ──

def test_raising_warn_drops_level_quietly(cfg):
    col, store, rec, clock = make(cfg)
    col.apply_swap(swap_reading(66))
    assert rec.kinds == ["swap_warning"]
    cfg.set("swap.warn", 70)       # 66 is inside 70-5: hysteresis holds WARNING
    col.reconfigure_swap()         # (also driven by the subscription in start())
    assert store.state.system.swap.level == LEVEL_WARNING
    assert store.state.system.swap.warn_at == 70.0
    cfg.set("swap.warn", 80)
    cfg.set("swap.crit", 90)
    col.reconfigure_swap()
    assert store.state.system.swap.level == LEVEL_OK
    clock.t += 10
    col.apply_swap(swap_reading(66))
    col.apply_swap(swap_reading(20))
    assert rec.kinds == ["swap_warning"]      # no re-fire, no "back to normal"


def test_quiet_drop_to_ok_closes_the_bubble(cfg):
    # A critical bubble never expires in GNOME; no recovery will replace it.
    closer = Closer()
    col, store, rec, _ = make(cfg, closer=closer)
    col.apply_swap(swap_reading(88))
    assert rec.kinds == ["swap_critical"]
    cfg.set("swap.crit", 97)           # 88 -> WARNING: quiet, bubble stays
    col.reconfigure_swap()
    assert store.state.system.swap.level == LEVEL_WARNING and closer.keys == []
    cfg.set("swap.warn", 95)           # 88 -> OK: quiet, bubble withdrawn
    col.reconfigure_swap()
    assert store.state.system.swap.level == LEVEL_OK
    assert closer.keys == ["swap"] and rec.kinds == ["swap_critical"]
    col.reconfigure_swap()             # already OK: nothing more to close
    assert closer.keys == ["swap"]


def test_crit_below_warn_is_clamped(cfg):
    cfg.set("swap.warn", 70)
    cfg.set("swap.crit", 50)
    col, store, _, _ = make(cfg)
    assert store.state.system.swap.crit_at == 70.0


def test_config_subscription_reconfigures(cfg, monkeypatch):
    col, store, rec, _ = make(cfg)
    col.apply_swap(swap_reading(90))
    col._unsubscribe = cfg.subscribe(col._on_config)
    cfg.set("swap.crit", 97)
    assert store.state.system.swap.crit_at == 97.0
    assert store.state.system.swap.level == LEVEL_WARNING   # dropped quietly
    assert rec.kinds == ["swap_critical"]


# ── CPU / memory / disk / net / gpu ──

def test_cpu_state_and_history(cfg):
    col, store, _, _ = make(cfg)
    col.apply_cpu({"PERCENTAGE": None, "TEMPERATURE": None, "CORES": 8})
    cpu = store.state.system.cpu
    assert cpu.percent is None and cpu.temp_c is None and cpu.cores == 8
    assert cpu.history == []
    for p in (10.04, 20.0, 30.0):
        col.apply_cpu({"PERCENTAGE": p, "TEMPERATURE": 61.2, "CORES": 8})
    cpu = store.state.system.cpu
    assert cpu.history == [10.0, 20.0, 30.0] and cpu.temp_c == 61.2


def test_history_export_is_15_minutes(cfg):
    col, store, _, _ = make(cfg)
    for i in range(400):
        col.apply_cpu({"PERCENTAGE": float(i % 100), "CORES": 4})
        col.apply_swap(swap_reading(10))
    assert len(store.state.system.cpu.history) == 300        # 3 s × 300 = 15 min
    assert len(store.state.system.swap.history) == 90        # 10 s × 90 = 15 min
    assert export_points(5) == 180 and export_points(60) == 15


def test_history_covers_15_minutes_at_a_themed_2s_interval(cfg):
    # The shipped default.yaml polls NET at 2 s: 300 points would be 10 min.
    theme = Theme(metrics=[MetricRender("NET", "DOWNLOAD", interval=2),
                           MetricRender("CPU", "PERCENTAGE", interval=1)])
    col, store, _, _ = make(cfg, theme)
    for i in range(1200):
        col.apply_net({"DOWNLOAD_BPS": float(i), "UPLOAD_BPS": 1.0, "IFACE": "all"})
        col.apply_cpu({"PERCENTAGE": 5.0, "CORES": 4})
    n = store.state.system.net
    assert len(n.history_down) * col.intervals["NET"] == 900
    assert n.history_down[-1] == 1199.0
    assert len(store.state.system.cpu.history) == 900        # 1 s × 900
    assert collect.ring_size(3) == 300 and collect.ring_size(2) == 450


def test_memory_state(cfg):
    col, store, _, _ = make(cfg)
    col.apply_memory({"PERCENTAGE": 68.0, "USED": 10.8, "TOTAL": 15.6})
    m = store.state.system.memory
    assert (m.percent, m.used_gb, m.total_gb, m.history) == (68.0, 10.8, 15.6, [68.0])


def test_disk_uses_configured_mount(cfg, tmp_path):
    cfg.set("disk.mount", str(tmp_path))
    col, store, _, _ = make(cfg)
    col.tick("DISK")
    d = store.state.system.disk
    assert d.mount == str(tmp_path) and d.total_gb and d.percent is not None


class Deferred:
    """Stand-ins for a thread and GLib.idle_add: run only when told to."""

    def __init__(self):
        self.work, self.posted = [], []

    def spawn(self, fn):
        self.work.append(fn)

    def post(self, fn, *args):
        self.posted.append((fn, args))

    def run_all(self):
        while self.work:
            self.work.pop(0)()
        while self.posted:
            fn, args = self.posted.pop(0)
            fn(*args)


def test_disk_reads_off_loop_and_never_piles_up(cfg, tmp_path):
    cfg.set("disk.mount", str(tmp_path))
    col, store, _, _ = make(cfg)
    d = Deferred()
    assert col.tick_async("DISK", spawn=d.spawn, post=d.post) is True
    # A hung mount: the read hasn't returned, so the next tick does nothing.
    assert col.tick_async("DISK", spawn=d.spawn, post=d.post) is False
    assert len(d.work) == 1 and store.state.system.disk.percent is None
    d.work.pop(0)()                     # the "thread" finishes...
    assert store.state.system.disk.percent is None   # ...but only posts
    fn, args = d.posted.pop(0)
    assert fn(*args) is False           # main loop applies; one-shot idle
    assert store.state.system.disk.mount == str(tmp_path)
    assert store.state.system.disk.percent is not None
    assert col.tick_async("DISK", spawn=d.spawn, post=d.post) is True


def test_disk_mount_change_mid_read_rereads(cfg, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    cfg.set("disk.mount", str(tmp_path))
    col, store, _, _ = make(cfg)
    d = Deferred()
    col.tick_async("DISK", spawn=d.spawn, post=d.post)
    cfg.set("disk.mount", str(other))   # changed while the first read runs
    d.work.pop(0)()
    fn, args = d.posted.pop(0)
    # The stale result is applied, then a fresh read for the new mount starts.
    import smartpanel.collect.system as m
    real_tick_async = col.tick_async
    calls = []
    col.tick_async = lambda cat, **kw: calls.append(cat) or real_tick_async(
        cat, spawn=d.spawn, post=d.post)
    fn(*args)
    assert calls == ["DISK"]
    d.run_all()
    assert store.state.system.disk.mount == str(other)
    assert m.OFF_LOOP == {"DISK"}


def test_disk_unreadable_mount(cfg):
    cfg.set("disk.mount", "/no/such/mount")
    col, store, _, _ = make(cfg)
    col.tick("DISK")
    d = store.state.system.disk
    assert d.mount == "/no/such/mount" and d.percent is None and d.used_gb is None


def test_net_state(cfg):
    col, store, _, _ = make(cfg)
    col.apply_net({"DOWNLOAD_BPS": None, "UPLOAD_BPS": None, "IFACE": "enp4s0"})
    n = store.state.system.net
    assert n.down_bps is None and n.iface == "enp4s0" and n.history_down == []
    col.apply_net({"DOWNLOAD_BPS": 2_400_000.4, "UPLOAD_BPS": 180_000.0, "IFACE": "all"})
    n = store.state.system.net
    assert n.down_bps == 2_400_000 and n.history_up == [180_000]


def test_gpu_state(cfg):
    col, store, _, _ = make(cfg)
    col.apply_gpu({"AVAILABLE": False, "VENDOR": None, "PERCENTAGE": None})
    g = store.state.system.gpu
    assert g.available is False and g.vendor is None and g.history == []
    col.apply_gpu({"AVAILABLE": True, "VENDOR": "amd", "PERCENTAGE": 37.0,
                   "MEMORY_USED": 1.336, "MEMORY_TOTAL": 11.984, "TEMPERATURE": 39.0})
    g = store.state.system.gpu
    assert (g.available, g.vendor, g.percent, g.vram_used_gb, g.vram_total_gb,
            g.temp_c) == (True, "amd", 37.0, 1.34, 11.98, 39.0)


def test_failing_reader_never_raises(cfg):
    def boom():
        raise RuntimeError("sensor died")
    col, store, _, _ = make(cfg, CPU=boom)
    assert col.tick("CPU") is True
    assert store.state.system.cpu.percent is None


def test_notifier_errors_are_contained(cfg):
    store = Store()

    def bad(*a, **k):
        raise OSError("dbus")
    col = SystemCollector(store, cfg, readers={}, notifier=bad, clock=lambda: 0.0)
    col.apply_swap(swap_reading(95))
    assert store.state.system.swap.level == LEVEL_CRITICAL


def test_state_is_json_serializable(cfg):
    col, store, _, _ = make(cfg)
    for cat in collect.CATEGORIES:
        col.tick(cat)             # real sensors, one read each
    js = store.to_json({"system"})
    assert '"swap"' in js and '"gpu"' in js


# ── intervals ──

def test_intervals_default_and_theme():
    assert collect.intervals_for(None) == collect.DEFAULT_INTERVALS
    theme = Theme(metrics=[MetricRender("CPU", "PERCENTAGE", interval=1),
                           MetricRender("CPU", "TEMPERATURE", interval=5),
                           MetricRender("NET", "DOWNLOAD", interval=2),
                           MetricRender("CLAUDE", "COST_TODAY", interval=15)])
    iv = collect.intervals_for(theme)
    assert iv["CPU"] == 1 and iv["NET"] == 2 and iv["SWAP"] == 10
    assert "CLAUDE" not in iv


def test_start_and_stop_schedule_timers(cfg):
    col, store, _, _ = make(cfg)
    col.start()
    try:
        assert len(col._timers) == len(collect.CATEGORIES)
        assert store.state.system.updated_at is not None
        assert store.state.system.memory.total_gb
    finally:
        col.stop()
    assert col._timers == [] and col._unsubscribe is None


# ── copy ──

def test_swap_notification_copy():
    L = alerts.Level
    assert swap_notification(L.WARNING, 66, 5.28, 8.0) == (
        "swap_warning", "Swap is filling up",
        "66% used (5.3 of 8 GB). Close something heavy before the system stalls.")
    assert swap_notification(L.OK, None, None, None)[2] == \
        "No swap configured. The alert is cleared."
