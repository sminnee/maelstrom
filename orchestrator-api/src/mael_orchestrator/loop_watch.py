"""The loop-stall watchdog: how long the server's one event loop was held.

See "Loop stalls" in ``docs/dev/orchestrator-server.md``.
"""

import asyncio
import io
import logging
import time
from collections.abc import Awaitable, Callable

from mael_common.util import now_iso

log = logging.getLogger(__name__)

#: How long the watch sleeps between two ticks.
TICK_SECS = 0.1
#: A gap between two ticks longer than this is a stall.
STALL_SECS = 0.25


class LoopWatch:
    """Measures the gaps between ticks of the running loop."""

    def __init__(
        self,
        *,
        interval: float = TICK_SECS,
        threshold: float = STALL_SECS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], str] = now_iso,
    ) -> None:
        self._interval = interval
        self._threshold = threshold
        self._clock = clock
        self._sleep = sleep
        self._now = now
        self._max_gap = 0.0
        self._last_stall: str | None = None

    def reading(self) -> dict[str, int | str | None]:
        """The longest gap since start, and when the last stall was seen."""
        return {
            "maxGapMs": round(self._max_gap * 1000),
            "lastStallAt": self._last_stall,
        }

    async def run(self) -> None:
        """Tick forever; cancel to stop."""
        last = self._clock()
        while True:
            await self._sleep(self._interval)
            tick = self._clock()
            gap = tick - last
            last = tick
            self._max_gap = max(self._max_gap, gap)
            if gap > self._threshold:
                self._last_stall = self._now()
                log.warning(
                    "event loop stalled for %d ms; tasks now:\n%s",
                    round(gap * 1000),
                    _task_stacks(),
                )
                # The report itself takes time; it must not count as the next gap.
                last = self._clock()


def _task_stacks() -> str:
    """Every task but the current one, each with its stack."""
    out = io.StringIO()
    current = asyncio.current_task()
    for task in asyncio.all_tasks():
        if task is not current:
            task.print_stack(limit=8, file=out)
    return out.getvalue()
