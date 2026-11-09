"""Machine metrics: read the machine through psutil, its one caller here.

See CONTEXT.md, "Machine metrics", and orchestrator-server.md, "Machine metrics".
``hubs.MetricsHub`` keeps the samples for the sockets.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import psutil

log = logging.getLogger(__name__)

#: How often the sampler reads the machine.
METRICS_PERIOD_SECS = 15.0
#: The sensor groups that report the CPU, most specific first.
CPU_SENSORS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz")
#: Per group, the entry that is the whole package, when there is one.
PACKAGE_LABELS = {"coretemp": "Package id 0", "k10temp": "Tctl"}


@dataclass(frozen=True)
class MachineReading:
    """The machine's figures at one moment, before the gate jobs are added."""

    cpu_percent: float
    cpu_count: int
    memory_used_bytes: int
    memory_total_bytes: int
    cpu_temperature_c: float | None


def pick_cpu_temperature(sensors: Mapping[str, Sequence[Any]]) -> float | None:
    """The CPU temperature from ``psutil.sensors_temperatures()``, or ``None``.

    The first group of ``CPU_SENSORS`` present wins. Its package entry is used
    when it has one; otherwise its highest ``current``.
    """
    for group in CPU_SENSORS:
        entries = [e for e in sensors.get(group) or [] if e.current is not None]
        if not entries:
            continue
        package = PACKAGE_LABELS.get(group)
        for entry in entries:
            if package is not None and entry.label == package:
                return float(entry.current)
        return float(max(e.current for e in entries))
    return None


_primed = False
#: Whether a sensor read has failed, so a lasting failure logs once.
_sensor_failed = False


def read_machine() -> MachineReading:
    """Read the machine through psutil. Blocking: run it off the loop.

    The CPU figure is the mean since the previous call. The first call has no
    previous one, so it waits half a second for a figure of its own.
    """
    global _primed  # noqa: PLW0603 — psutil itself keeps the previous reading
    cpu = psutil.cpu_percent(interval=None if _primed else 0.5)
    _primed = True
    memory = psutil.virtual_memory()
    return MachineReading(
        cpu_percent=cpu,
        cpu_count=psutil.cpu_count() or 1,
        memory_used_bytes=memory.total - memory.available,
        memory_total_bytes=memory.total,
        cpu_temperature_c=_temperature(),
    )


def _temperature() -> float | None:
    read = getattr(psutil, "sensors_temperatures", None)
    if read is None:  # macOS has no sensors API
        return None
    global _sensor_failed  # noqa: PLW0603
    try:
        return pick_cpu_temperature(read())
    except Exception:  # noqa: BLE001 — a bad sensor must not stop the sample
        if not _sensor_failed:
            log.warning("CPU sensor read failed", exc_info=True)
        _sensor_failed = True
        return None
