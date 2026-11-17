"""System sensors: CPU / MEMORY / SWAP / DISK / NET / BATTERY, via psutil.

Every public function here returns `{METRIC: value}` and **never raises**: an
unreadable value is `None` (rendered as "—"), never 0. The v1 indicator and the
v2 `collect.system.SystemCollector` both read these.

The pure parts (temperature source choice, the default-route parse, virtual
interface filtering, per-interface rate maths) are split out so they can be
tested with real structures and fake counters, no hardware needed.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Mapping

import psutil

log = logging.getLogger(__name__)

GIB = 1024 ** 3
MIB = 1024 ** 2

# ───────────────────────── CPU ─────────────────────────

# psutil.cpu_percent(interval=None) compares against the previous call; the very
# first call has nothing to compare with and returns a meaningless 0.0. We
# report None ("unknown") for that one call instead of a fake idle reading.
_cpu_primed = False

# SP-0202: the CPU temperature source, in order of preference. `cpu_thermal`
# is the ARM SoC sensor (Raspberry Pi and friends), still a CPU reading.
# Anything else (nvme, amdgpu, iwlwifi, ...) is not the CPU and is never used:
# a missing sensor reads "—", never some other chip's temperature.
TEMP_SOURCES = ("coretemp", "k10temp", "zenpower", "acpitz", "cpu_thermal")
# Within a source, the label that means "the whole package" when there is one.
_PACKAGE_LABELS = ("package id 0", "tctl", "tdie", "tccd1")
_logged_temp_source: str | None = None


def pick_cpu_temp(temps: Mapping[str, list] | None) -> tuple[str, float] | None:
    """Choose `(source, °C)` from `psutil.sensors_temperatures()` output.

    Pure. Returns None when none of `TEMP_SOURCES` is present with a reading.
    """
    if not temps:
        return None
    for source in TEMP_SOURCES:
        entries = [e for e in (temps.get(source) or [])
                   if getattr(e, "current", None) is not None]
        if not entries:
            continue
        by_label = {(e.label or "").lower(): e for e in entries}
        for label in _PACKAGE_LABELS:
            if label in by_label:
                return source, float(by_label[label].current)
        return source, float(entries[0].current)
    return None


def _read_cpu_temp() -> float | None:
    global _logged_temp_source
    try:
        temps = psutil.sensors_temperatures()
    except (AttributeError, OSError) as e:
        log.debug("sensors_temperatures failed: %s", e)
        return None
    picked = pick_cpu_temp(temps)
    if picked is None:
        return None
    source, value = picked
    if _logged_temp_source != source:
        log.info("CPU temperature source: %s", source)
        _logged_temp_source = source
    return value


def cpu() -> dict[str, float | str | int | None]:
    global _cpu_primed
    try:
        pct: float | None = psutil.cpu_percent(interval=None)
    except Exception as e:  # noqa: BLE001 — sensors never raise
        log.debug("cpu_percent failed: %s", e)
        pct = None
    if not _cpu_primed:
        _cpu_primed = True
        pct = None
    try:
        freq = psutil.cpu_freq()
    except Exception:  # noqa: BLE001
        freq = None
    try:
        load = psutil.getloadavg()
    except (AttributeError, OSError):
        load = (None, None, None)
    return {
        "PERCENTAGE": pct,
        "FREQUENCY": (freq.current if freq else None),  # MHz
        "LOAD_ONE": load[0],
        "LOAD_FIVE": load[1],
        "LOAD_FIFTEEN": load[2],
        "TEMPERATURE": _read_cpu_temp(),
        "CORES": psutil.cpu_count(logical=True) or 0,
    }


# ───────────────────────── memory / swap ─────────────────────────

def memory() -> dict[str, float | None]:
    """RAM. `USED` is total − available, so it agrees with `PERCENTAGE`
    ("10.8 of 16 GB" next to "68%"); psutil's own `used` excludes cache and
    would read lower than the percentage claims."""
    try:
        vm = psutil.virtual_memory()
        sm = psutil.swap_memory()
    except Exception as e:  # noqa: BLE001
        log.debug("virtual_memory failed: %s", e)
        return {"PERCENTAGE": None, "USED": None, "FREE": None, "TOTAL": None,
                "SWAP_PERCENT": None, "SWAP_USED": None}
    return {
        "PERCENTAGE": vm.percent,
        "USED": (vm.total - vm.available) / GIB,
        "FREE": vm.available / GIB,
        "TOTAL": vm.total / GIB,
        "SWAP_PERCENT": sm.percent,
        "SWAP_USED": sm.used / GIB,
    }


def swap() -> dict[str, float | None]:
    """Swap usage as its own category, so it can carry the near-full alert.

    `MEMORY.SWAP_*` still exists for themes that bind to it; this is the
    canonical source the alert reads. A box with swap disabled reports
    TOTAL == 0, and PERCENTAGE is None rather than 0 so the renderer shows "—"
    and `alerts.raw_level` treats it as permanently OK instead of "0% full".
    """
    try:
        sm = psutil.swap_memory()
    except Exception as e:  # noqa: BLE001
        log.debug("swap_memory failed: %s", e)
        return {"PERCENTAGE": None, "USED": None, "FREE": None, "TOTAL": None}
    has_swap = sm.total > 0
    return {
        "PERCENTAGE": (sm.percent if has_swap else None),
        "USED": sm.used / GIB,
        "FREE": sm.free / GIB,
        "TOTAL": sm.total / GIB,
    }


# ───────────────────────── disk ─────────────────────────

def disk(mount: str = "/") -> dict[str, float | str | None]:
    """Usage of one filesystem (SP-0401: `disk.mount`, default `/`).

    An unreadable or missing mount gives None values rather than raising.
    """
    mount = mount or "/"
    try:
        du = psutil.disk_usage(mount)
    except Exception as e:  # noqa: BLE001 — OSError, PermissionError, ...
        log.debug("disk_usage(%r) failed: %s", mount, e)
        return {"MOUNT": mount, "USED_PERCENT": None, "USED": None,
                "FREE": None, "TOTAL": None}
    return {
        "MOUNT": mount,
        "USED_PERCENT": du.percent,
        "USED": du.used / GIB,
        "FREE": du.free / GIB,
        "TOTAL": du.total / GIB,
    }


# ───────────────────────── network ─────────────────────────

# SP-0502: interfaces whose traffic is a copy of traffic already counted on a
# physical NIC (containers, bridges, VPN tunnels) or never leaves the box.
VIRTUAL_PREFIXES = ("docker", "veth", "br-", "virbr", "wg")
VIRTUAL_NAMES = ("lo",)
ALL_IFACES = "all"
PROC_NET_ROUTE = "/proc/net/route"


def is_virtual(iface: str) -> bool:
    return iface in VIRTUAL_NAMES or iface.startswith(VIRTUAL_PREFIXES)


def default_route_iface(route_text: str | None) -> str | None:
    """The interface carrying the IPv4 default route, from `/proc/net/route`.

    Pure. Destination and mask both 0 means default; with several, the lowest
    metric wins (that's the one the kernel uses). Only routes marked up
    (RTF_UP, flag 0x1) count.
    """
    if not route_text:
        return None
    best: tuple[int, str] | None = None
    for line in route_text.splitlines()[1:]:
        cols = line.split()
        if len(cols) < 8:
            continue
        iface, dest, flags, metric, mask = cols[0], cols[1], cols[3], cols[6], cols[7]
        try:
            if int(dest, 16) != 0 or int(mask, 16) != 0 or not int(flags, 16) & 0x1:
                continue
            m = int(metric)
        except ValueError:
            continue
        if best is None or m < best[0]:
            best = (m, iface)
    return best[1] if best else None


def _read_route() -> str | None:
    try:
        with open(PROC_NET_ROUTE, encoding="ascii", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


Counters = Mapping[str, Any]   # iface -> object with bytes_recv / bytes_sent


def sum_rates(prev: Counters, cur: Counters, dt: float) -> tuple[float, float]:
    """Summed (down, up) bytes/s across physical interfaces. Pure.

    Only interfaces present in both samples count (one that appears between
    ticks would otherwise contribute its whole lifetime total), and a counter
    that went backwards (driver reset, iface re-created) contributes 0.
    """
    dt = max(dt, 1e-3)
    down = up = 0
    for name, c in cur.items():
        if is_virtual(name) or name not in prev:
            continue
        p = prev[name]
        down += max(c.bytes_recv - p.bytes_recv, 0)
        up += max(c.bytes_sent - p.bytes_sent, 0)
    return down / dt, up / dt


class NetMeter:
    """Rate meter over per-interface counters. Every input is injectable."""

    def __init__(self,
                 counters: Callable[[], Counters] | None = None,
                 route_text: Callable[[], str | None] = _read_route,
                 clock: Callable[[], float] = time.monotonic):
        self._counters = counters or (lambda: psutil.net_io_counters(pernic=True))
        self._route_text = route_text
        self._clock = clock
        self._prev: Counters | None = None
        self._prev_ts: float | None = None

    def read(self) -> dict[str, float | str | None]:
        try:
            cur = dict(self._counters() or {})
        except Exception as e:  # noqa: BLE001
            log.debug("net counters failed: %s", e)
            cur = {}
        now = self._clock()
        try:
            iface = default_route_iface(self._route_text()) or ALL_IFACES
        except Exception:  # noqa: BLE001
            iface = ALL_IFACES
        down_bps = up_bps = None
        if self._prev is not None and self._prev_ts is not None and cur:
            down_bps, up_bps = sum_rates(self._prev, cur, now - self._prev_ts)
        if cur:
            self._prev, self._prev_ts = cur, now
        phys = [c for n, c in cur.items() if not is_virtual(n)]
        total_recv = sum(c.bytes_recv for c in phys) if phys else None
        total_sent = sum(c.bytes_sent for c in phys) if phys else None
        return {
            # v1 keys, MB/s (see render.UNITS)
            "DOWNLOAD": (down_bps / MIB if down_bps is not None else None),
            "UPLOAD": (up_bps / MIB if up_bps is not None else None),
            "DOWNLOADED": (total_recv / GIB if total_recv is not None else None),
            "UPLOADED": (total_sent / GIB if total_sent is not None else None),
            # v2 keys, bytes/s (NetState)
            "DOWNLOAD_BPS": down_bps,
            "UPLOAD_BPS": up_bps,
            "IFACE": iface,
        }


_net_meter = NetMeter()


def network() -> dict[str, float | str | None]:
    """Rates since the previous call. The first call has no baseline: None."""
    return _net_meter.read()


# ───────────────────────── battery ─────────────────────────

def battery() -> dict[str, float | str | None]:
    try:
        b = psutil.sensors_battery()
    except Exception:  # noqa: BLE001
        b = None
    if b is None:
        return {"PERCENTAGE": None, "PLUGGED": None, "SECS_LEFT": None}
    return {
        "PERCENTAGE": b.percent,
        "PLUGGED": "AC" if b.power_plugged else "Battery",
        "SECS_LEFT": (b.secsleft if b.secsleft is not None and b.secsleft >= 0 else None),
    }
