"""GPU sensor: NVIDIA via pynvml, AMD via sysfs, graceful "no GPU" otherwise.

`gpu()` always returns the same keys (SP-0601):

    AVAILABLE      bool   — a sensor answered; the menu hides GPU rows when False
    VENDOR         "nvidia" | "amd" | None
    PERCENTAGE     busy %
    MEMORY_PERCENT / MEMORY_USED / MEMORY_TOTAL   VRAM (GiB)
    TEMPERATURE    °C

AMD reads `/sys/class/drm/card<N>/device/{gpu_busy_percent, mem_info_vram_used,
mem_info_vram_total}` and the `hwmon*/temp1_input` of that device. These are plain
file reads (~0.1 ms), so they are fine on the main loop. `amd_gpu(root)` takes the
drm root so tests point it at a fake tree. Never raises.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

GIB = 1024 ** 3
DRM_ROOT = "/sys/class/drm"
AMD_VENDOR_ID = 0x1002
_CARD_RE = re.compile(r"^card\d+$")   # card1, not card1-DP-1 (a connector)

_pynvml = None
_nvml_init_failed = False


def _empty() -> dict[str, float | str | bool | None]:
    return {"AVAILABLE": False, "VENDOR": None, "PERCENTAGE": None,
            "MEMORY_PERCENT": None, "MEMORY_USED": None, "MEMORY_TOTAL": None,
            "TEMPERATURE": None}


# ───────────────────────── NVIDIA ─────────────────────────

def _try_pynvml():
    global _pynvml, _nvml_init_failed
    if _pynvml is not None or _nvml_init_failed:
        return _pynvml
    try:
        import pynvml  # type: ignore
        pynvml.nvmlInit()
        _pynvml = pynvml
    except Exception as e:  # noqa: BLE001
        log.debug("pynvml unavailable: %s", e)
        _nvml_init_failed = True
    return _pynvml


def nvidia_gpu() -> dict | None:
    nvml = _try_pynvml()
    if nvml is None:
        return None
    try:
        h = nvml.nvmlDeviceGetHandleByIndex(0)
        util = nvml.nvmlDeviceGetUtilizationRates(h)
        mem = nvml.nvmlDeviceGetMemoryInfo(h)
        temp = nvml.nvmlDeviceGetTemperature(h, nvml.NVML_TEMPERATURE_GPU)
        out = _empty()
        out.update({
            "AVAILABLE": True,
            "VENDOR": "nvidia",
            "PERCENTAGE": float(util.gpu),
            "MEMORY_PERCENT": (float(mem.used) / float(mem.total) * 100.0
                               if mem.total else None),
            "MEMORY_USED": mem.used / GIB,
            "MEMORY_TOTAL": mem.total / GIB,
            "TEMPERATURE": float(temp),
        })
        return out
    except Exception as e:  # noqa: BLE001
        log.debug("nvml read failed: %s", e)
        return None


# ───────────────────────── AMD ─────────────────────────

def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip(), 0)
    except (OSError, ValueError):
        return None


def find_amd_device(root: str | Path = DRM_ROOT) -> Path | None:
    """The first `card<N>/device` that is AMD and exposes `gpu_busy_percent`."""
    try:
        cards = sorted((p for p in Path(root).iterdir() if _CARD_RE.match(p.name)),
                       key=lambda p: int(p.name[4:]))
    except OSError:
        return None
    for card in cards:
        dev = card / "device"
        vendor = _read_int(dev / "vendor")
        if vendor is not None and vendor != AMD_VENDOR_ID:
            continue
        if (dev / "gpu_busy_percent").exists():
            return dev
    return None


def _amd_temp(dev: Path) -> float | None:
    try:
        hwmons = sorted((dev / "hwmon").glob("hwmon*"))
    except OSError:
        return None
    for hw in hwmons:
        milli = _read_int(hw / "temp1_input")   # "edge" on amdgpu
        if milli is not None:
            return milli / 1000.0
    return None


def amd_gpu(root: str | Path = DRM_ROOT) -> dict | None:
    """AMD reading from sysfs, or None when there is no AMD GPU under `root`."""
    dev = find_amd_device(root)
    if dev is None:
        return None
    busy = _read_int(dev / "gpu_busy_percent")
    if busy is None:
        return None
    used = _read_int(dev / "mem_info_vram_used")
    total = _read_int(dev / "mem_info_vram_total")
    out = _empty()
    out.update({
        "AVAILABLE": True,
        "VENDOR": "amd",
        "PERCENTAGE": float(busy),
        "MEMORY_USED": (used / GIB if used is not None else None),
        "MEMORY_TOTAL": (total / GIB if total else None),
        "MEMORY_PERCENT": (used / total * 100.0 if used is not None and total else None),
        "TEMPERATURE": _amd_temp(dev),
    })
    return out


# ───────────────────────── public ─────────────────────────

def gpu(drm_root: str | Path = DRM_ROOT) -> dict[str, float | str | bool | None]:
    """NVIDIA first, then AMD, else `AVAILABLE: False` with None values."""
    try:
        return nvidia_gpu() or amd_gpu(drm_root) or _empty()
    except Exception as e:  # noqa: BLE001 — sensors never raise
        log.debug("gpu read failed: %s", e)
        return _empty()
