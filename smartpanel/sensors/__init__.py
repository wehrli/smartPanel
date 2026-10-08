from __future__ import annotations

from typing import Callable

from smartpanel.sensors import system, gpu, claude, sessions as sessions_sensor

SensorFn = Callable[[], dict[str, float | str | None]]

SENSORS: dict[str, SensorFn] = {
    "CPU": system.cpu,
    "MEMORY": system.memory,
    "SWAP": system.swap,
    "DISK": system.disk,
    "NET": system.network,
    "BATTERY": system.battery,
    "GPU": gpu.gpu,
    "CLAUDE": claude.claude_stats,
    "SESSIONS": sessions_sensor.sessions,
}


def read(category: str) -> dict[str, float | str | None]:
    fn = SENSORS.get(category)
    if fn is None:
        return {}
    try:
        return fn() or {}
    except Exception as e:  # never let a sensor crash the loop
        import logging
        logging.getLogger(__name__).warning("sensor %s failed: %s", category, e)
        return {}
