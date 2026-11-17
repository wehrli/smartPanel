"""System sensors: temperature source, default route, virtual ifaces, rates, disk.

psutil's own structures (`shwtemp`, `snetio`) are used as-is so the tests feed
the sensors exactly what the library hands them on a real box.
"""
from pathlib import Path

from psutil._common import shwtemp, snetio

from smartpanel.sensors import system

FIXTURES = Path(__file__).parent / "fixtures" / "system"


def t(label, current):
    return shwtemp(label, current, None, None)


# ── SP-0201 / SP-0202: CPU temperature ──

def test_temp_prefers_coretemp_package():
    temps = {"coretemp": [t("Core 0", 50.0), t("Package id 0", 61.0)],
             "acpitz": [t("", 40.0)]}
    assert system.pick_cpu_temp(temps) == ("coretemp", 61.0)


def test_temp_k10temp_uses_tctl():
    # Captured from this dev box (Ryzen + amdgpu).
    temps = {"k10temp": [t("Tctl", 42.25), t("Tccd1", 45.0)],
             "amdgpu": [t("edge", 38.0), t("junction", 41.0), t("mem", 40.0)]}
    assert system.pick_cpu_temp(temps) == ("k10temp", 42.25)


def test_temp_chain_order():
    temps = {"acpitz": [t("", 40.0)], "zenpower": [t("Tdie", 55.0)]}
    assert system.pick_cpu_temp(temps) == ("zenpower", 55.0)
    assert system.pick_cpu_temp({"acpitz": [t("", 40.0), t("", 41.0)]}) == ("acpitz", 40.0)


def test_temp_missing_cpu_sensors_is_none_not_another_chip():
    # No coretemp/k10temp/zenpower/acpitz: the GPU and the SSD are not the CPU.
    temps = {"amdgpu": [t("edge", 38.0)], "nvme": [t("Composite", 35.0)]}
    assert system.pick_cpu_temp(temps) is None
    assert system.pick_cpu_temp({}) is None
    assert system.pick_cpu_temp(None) is None


def test_cpu_reports_cores_and_never_zero_on_first_call(monkeypatch):
    monkeypatch.setattr(system, "_cpu_primed", False)
    first = system.cpu()
    assert first["PERCENTAGE"] is None           # no baseline yet: unknown, not 0
    assert first["CORES"] >= 1
    second = system.cpu()
    assert isinstance(second["PERCENTAGE"], float)


# ── SP-0301: memory ──

def test_memory_used_agrees_with_percentage():
    m = system.memory()
    assert m["TOTAL"] > 0
    assert abs(m["USED"] / m["TOTAL"] * 100 - m["PERCENTAGE"]) < 2.0


# ── SP-0401: disk ──

def test_disk_reads_configured_mount(tmp_path):
    d = system.disk(str(tmp_path))
    assert d["MOUNT"] == str(tmp_path)
    assert d["TOTAL"] > 0 and 0 <= d["USED_PERCENT"] <= 100


def test_disk_unreadable_mount_gives_none():
    d = system.disk("/definitely/not/a/mount/point")
    assert d["MOUNT"] == "/definitely/not/a/mount/point"
    assert d["USED_PERCENT"] is None and d["USED"] is None and d["TOTAL"] is None


# ── SP-0501: default route ──

def test_default_route_from_real_capture():
    text = (FIXTURES / "proc_net_route_docker.txt").read_text()
    assert system.default_route_iface(text) == "enp4s0"


def test_default_route_lowest_metric_wins_and_down_routes_skipped():
    text = (
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
        "wlp2s0\t00000000\t0101A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0\n"
        "enp4s0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
        "wg0\t00000000\t00000000\t0000\t0\t0\t50\t00000000\t0\t0\t0\n"
    )
    assert system.default_route_iface(text) == "enp4s0"


def test_no_default_route():
    header = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
    assert system.default_route_iface(header) is None
    assert system.default_route_iface("") is None
    assert system.default_route_iface(None) is None
    assert system.default_route_iface(header + "garbage line\n") is None


# ── SP-0502: virtual interfaces ──

def test_virtual_interface_names():
    for name in ("lo", "docker0", "veth26a4acd", "br-04324bc6ca03", "virbr0", "wg0"):
        assert system.is_virtual(name), name
    for name in ("enp4s0", "wlp3s0", "eth0", "tailscale0", "bridge"):
        assert not system.is_virtual(name), name


def io(recv, sent):
    return snetio(sent, recv, 0, 0, 0, 0, 0, 0)


def test_rates_exclude_container_traffic():
    prev = {"enp4s0": io(1000, 500), "docker0": io(0, 0), "veth1": io(0, 0),
            "lo": io(0, 0)}
    # A container pulls 4 MB: it shows on enp4s0 AND on docker0 and the veth.
    cur = {"enp4s0": io(1000 + 4_000_000, 500 + 2000),
           "docker0": io(4_000_000, 0), "veth1": io(4_000_000, 0),
           "lo": io(9_999_999, 9_999_999)}
    down, up = system.sum_rates(prev, cur, 2.0)
    assert down == 2_000_000
    assert up == 1000


def test_rates_ignore_new_and_reset_interfaces():
    prev = {"enp4s0": io(5000, 5000)}
    cur = {"enp4s0": io(100, 100),          # counter reset
           "wlp3s0": io(10**9, 10**9)}      # appeared since last tick
    assert system.sum_rates(prev, cur, 1.0) == (0.0, 0.0)


def test_net_meter_first_reading_is_unknown_then_rates():
    samples = iter([
        {"enp4s0": io(0, 0), "docker0": io(0, 0)},
        {"enp4s0": io(3_000_000, 300_000), "docker0": io(3_000_000, 0)},
    ])
    clock = iter([10.0, 13.0])
    route = (FIXTURES / "proc_net_route_docker.txt").read_text()
    meter = system.NetMeter(counters=lambda: next(samples),
                            route_text=lambda: route, clock=lambda: next(clock))
    first = meter.read()
    assert first["DOWNLOAD_BPS"] is None and first["DOWNLOAD"] is None
    assert first["IFACE"] == "enp4s0"
    second = meter.read()
    assert second["DOWNLOAD_BPS"] == 1_000_000
    assert second["UPLOAD_BPS"] == 100_000
    assert abs(second["DOWNLOAD"] - 1_000_000 / 1024 ** 2) < 1e-9   # v1 key stays MB/s


def test_net_meter_falls_back_to_all_and_survives_errors():
    def boom():
        raise OSError("no /proc")
    meter = system.NetMeter(counters=boom, route_text=lambda: None, clock=lambda: 1.0)
    out = meter.read()
    assert out["IFACE"] == "all"
    assert out["DOWNLOAD_BPS"] is None and out["DOWNLOADED"] is None


def test_swap_sensor_shape():
    s = system.swap()
    assert set(s) == {"PERCENTAGE", "USED", "FREE", "TOTAL"}
    if s["TOTAL"] == 0:
        assert s["PERCENTAGE"] is None
