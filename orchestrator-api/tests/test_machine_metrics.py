"""The CPU sensor pick and the metrics hub."""

import asyncio
from types import SimpleNamespace
from typing import cast

from mael_domain.protocol import MetricsSample
from mael_orchestrator.hubs import LAGGING, MetricsHub
from mael_orchestrator.machine_metrics import pick_cpu_temperature


def sensor(label: str, current: float | None):
    return SimpleNamespace(label=label, current=current)


def test_the_package_entry_wins_over_a_hotter_core():
    sensors = {
        "acpitz": [sensor("", 90.0)],
        "coretemp": [sensor("Core 0", 70.0), sensor("Package id 0", 62.0)],
    }
    assert pick_cpu_temperature(sensors) == 62.0


def test_a_group_with_no_package_entry_gives_its_hottest():
    sensors = {"cpu_thermal": [sensor("", 48.5), sensor("", 51.0)]}
    assert pick_cpu_temperature(sensors) == 51.0


def test_the_first_group_in_pick_order_wins():
    sensors = {
        "acpitz": [sensor("", 30.0)],
        "k10temp": [sensor("Tccd1", 70.0), sensor("Tctl", 65.0)],
    }
    assert pick_cpu_temperature(sensors) == 65.0


def test_no_cpu_sensor_gives_none():
    sensors = {"nvme": [sensor("Composite", 40.0)], "coretemp": [sensor("x", None)]}
    assert pick_cpu_temperature(sensors) is None


def sample(n: int) -> MetricsSample:
    return cast(MetricsSample, {"at": str(n)})


def frame(n: int) -> dict:
    return {"type": "metrics.sample", "sample": sample(n)}


def test_the_hub_keeps_the_last_samples_oldest_first():
    hub = MetricsHub(limit=3)
    for n in range(5):
        hub.add(sample(n))
    with hub.subscribe() as (samples, _):
        assert [s["at"] for s in samples] == ["2", "3", "4"]


def test_a_subscriber_hears_each_sample_after_its_snapshot_once():
    async def scenario():
        hub = MetricsHub()
        hub.add(sample(1))
        with hub.subscribe() as (samples, subscriber):
            hub.add(sample(2))
            assert [s["at"] for s in samples] == ["1"]
            assert subscriber.queue.get_nowait() == frame(2)
            assert subscriber.queue.empty()
        hub.add(sample(3))
        assert subscriber.queue.empty()

    asyncio.run(scenario())


def test_a_subscriber_a_queue_behind_is_told_it_lags_and_dropped():
    async def scenario():
        hub = MetricsHub(queue_limit=2)
        with hub.subscribe() as (_, subscriber):
            for n in range(4):
                hub.add(sample(n))
            return [
                subscriber.queue.get_nowait() for _ in range(subscriber.queue.qsize())
            ]

    assert asyncio.run(scenario()) == [LAGGING]
