from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_pynvml = None
_nvml_init_failed = False


def _try_pynvml():
    global _pynvml, _nvml_init_failed
    if _pynvml is not None or _nvml_init_failed:
        return _pynvml
    try:
        import pynvml  # type: ignore
        pynvml.nvmlInit()
        _pynvml = pynvml
    except Exception as e:
        log.debug("pynvml unavailable: %s", e)
        _nvml_init_failed = True
    return _pynvml


def gpu() -> dict[str, float | str | None]:
    nvml = _try_pynvml()
    if nvml is None:
        return {"PERCENTAGE": None, "MEMORY_PERCENT": None, "TEMPERATURE": None}
    try:
        h = nvml.nvmlDeviceGetHandleByIndex(0)
        util = nvml.nvmlDeviceGetUtilizationRates(h)
        mem = nvml.nvmlDeviceGetMemoryInfo(h)
        temp = nvml.nvmlDeviceGetTemperature(h, nvml.NVML_TEMPERATURE_GPU)
        return {
            "PERCENTAGE": float(util.gpu),
            "MEMORY_PERCENT": float(mem.used) / float(mem.total) * 100.0,
            "MEMORY_USED": mem.used / (1024 ** 3),
            "MEMORY_TOTAL": mem.total / (1024 ** 3),
            "TEMPERATURE": float(temp),
        }
    except Exception as e:
        log.debug("nvml read failed: %s", e)
        return {"PERCENTAGE": None, "MEMORY_PERCENT": None, "TEMPERATURE": None}
