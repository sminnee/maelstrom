"""``LoopWatch``: the longest gap between two loop ticks, and a warning per stall."""

import asyncio
import logging

from mael_orchestrator.loop_watch import LoopWatch


class FakeTime:
    """A clock the watch reads, and a sleep that moves it by the interval asked."""

    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    async def sleep(self, secs: float) -> None:
        self.now += secs
        await asyncio.sleep(0)


def watch_through(
    fake: FakeTime, blocking_secs: float, *, report_secs: float = 0.0
) -> LoopWatch:
    """Run a watch for a few ticks while one task blocks the loop for ``blocking_secs``.

    ``report_secs`` is how long the watch's own stall report takes.
    """

    def now() -> str:
        fake.now += report_secs
        return "STALL-AT"

    watch = LoopWatch(
        interval=0.1, threshold=0.25, clock=fake.clock, sleep=fake.sleep, now=now
    )
    parked = asyncio.Event()

    def block() -> None:
        # A synchronous call on the loop: the time passes, nothing else runs.
        fake.now += blocking_secs

    async def blocker() -> None:
        block()
        await parked.wait()

    async def scenario() -> None:
        # Each fake sleep yields once, so a few yields let the watch tick, and
        # the blocker's jump lands between one tick's sleep and its clock read.
        watcher = asyncio.create_task(watch.run())
        for _ in range(3):
            await asyncio.sleep(0)
        held = asyncio.create_task(blocker(), name="the-blocker")
        for _ in range(5):
            await asyncio.sleep(0)
        watcher.cancel()
        parked.set()
        await asyncio.gather(watcher, held, return_exceptions=True)

    asyncio.run(scenario())
    return watch


def test_a_stall_sets_the_max_gap_and_logs_the_tasks(caplog):
    fake = FakeTime()
    with caplog.at_level(logging.WARNING, logger="mael_orchestrator.loop_watch"):
        watch = watch_through(fake, blocking_secs=0.5)

    # One 100 ms interval plus the 500 ms the blocking call held the loop:
    # over the 250 ms threshold.
    assert watch.reading() == {"maxGapMs": 600, "lastStallAt": "STALL-AT"}
    [record] = caplog.records
    assert "600 ms" in record.getMessage()
    assert "the-blocker" in record.getMessage()


def test_a_gap_under_the_threshold_is_no_stall(caplog):
    fake = FakeTime()
    with caplog.at_level(logging.WARNING, logger="mael_orchestrator.loop_watch"):
        watch = watch_through(fake, blocking_secs=0.1)

    # One 100 ms interval plus 100 ms held: under the 250 ms threshold.
    assert watch.reading() == {"maxGapMs": 200, "lastStallAt": None}
    assert caplog.records == []


def test_a_slow_stall_report_is_not_the_next_stall(caplog):
    """Printing every task's stack takes time on the loop. Counted as the next
    gap, each report would cause another."""
    fake = FakeTime()
    with caplog.at_level(logging.WARNING, logger="mael_orchestrator.loop_watch"):
        watch = watch_through(fake, blocking_secs=0.5, report_secs=0.3)

    assert len(caplog.records) == 1
    assert watch.reading()["maxGapMs"] == 600
