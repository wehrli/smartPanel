from __future__ import annotations

import time
from typing import Any

import psutil

# Track previous network counters for rate calculation.
_prev_net: dict[str, Any] = {"ts": None, "sent": 0, "recv": 0}


def cpu() -> dict[str, float | str | None]:
    freq = psutil.cpu_freq()
    temps = _read_cpu_temp()
    load = psutil.getloadavg()
    return {
        "PERCENTAGE": psutil.cpu_percent(interval=None),
        "FREQUENCY": (freq.current if freq else None),  # MHz
        "LOAD_ONE": load[0],
        "LOAD_FIVE": load[1],
        "LOAD_FIFTEEN": load[2],
        "TEMPERATURE": temps,
    }


def memory() -> dict[str, float | None]:
    vm = psutil.virtual_memory()
    sm = psutil.swap_memory()
    return {
        "PERCENTAGE": vm.percent,
        "USED": vm.used / (1024 ** 3),     # GiB
        "FREE": vm.available / (1024 ** 3),
        "TOTAL": vm.total / (1024 ** 3),
        "SWAP_PERCENT": sm.percent,
        "SWAP_USED": sm.used / (1024 ** 3),
    }


def swap() -> dict[str, float | None]:
    """Swap usage as its own category, so it can carry the near-full alert.

    `MEMORY.SWAP_*` still exists for themes that bind to it; this is the
    canonical source the alert reads. A box with swap disabled reports
    TOTAL == 0, and PERCENTAGE is None rather than 0 so the renderer shows "—"
    and `alerts.raw_level` treats it as permanently OK instead of "0% full".
    """
    sm = psutil.swap_memory()
    has_swap = sm.total > 0
    return {
        "PERCENTAGE": (sm.percent if has_swap else None),
        "USED": sm.used / (1024 ** 3),
        "FREE": sm.free / (1024 ** 3),
        "TOTAL": sm.total / (1024 ** 3),
    }


def disk() -> dict[str, float | None]:
    root = psutil.disk_usage("/")
    return {
        "USED_PERCENT": root.percent,
        "USED": root.used / (1024 ** 3),
        "FREE": root.free / (1024 ** 3),
        "TOTAL": root.total / (1024 ** 3),
    }


def network() -> dict[str, float | None]:
    now = time.monotonic()
    io = psutil.net_io_counters()
    prev_ts = _prev_net["ts"]
    if prev_ts is None:
        _prev_net.update(ts=now, sent=io.bytes_sent, recv=io.bytes_recv)
        return {"DOWNLOAD": 0.0, "UPLOAD": 0.0,
                "DOWNLOADED": io.bytes_recv / (1024 ** 3),
                "UPLOADED": io.bytes_sent / (1024 ** 3)}
    dt = max(now - prev_ts, 1e-3)
    down = (io.bytes_recv - _prev_net["recv"]) / dt / (1024 ** 2)  # MB/s
    up = (io.bytes_sent - _prev_net["sent"]) / dt / (1024 ** 2)
    _prev_net.update(ts=now, sent=io.bytes_sent, recv=io.bytes_recv)
    return {
        "DOWNLOAD": max(down, 0.0),
        "UPLOAD": max(up, 0.0),
        "DOWNLOADED": io.bytes_recv / (1024 ** 3),
        "UPLOADED": io.bytes_sent / (1024 ** 3),
    }


def battery() -> dict[str, float | str | None]:
    b = psutil.sensors_battery()
    if b is None:
        return {"PERCENTAGE": None, "PLUGGED": None, "SECS_LEFT": None}
    return {
        "PERCENTAGE": b.percent,
        "PLUGGED": "AC" if b.power_plugged else "Battery",
        "SECS_LEFT": (b.secsleft if b.secsleft is not None and b.secsleft >= 0 else None),
    }


def _read_cpu_temp() -> float | None:
    try:
        temps = psutil.sensors_temperatures()
    except AttributeError:
        return None
    if not temps:
        return None
    # Prefer common labels in order
    for key in ("coretemp", "k10temp", "zenpower", "acpitz", "cpu_thermal"):
        entries = temps.get(key)
        if entries:
            return entries[0].current
    # Fallback: first available
    first = next(iter(temps.values()), None)
    return first[0].current if first else None
