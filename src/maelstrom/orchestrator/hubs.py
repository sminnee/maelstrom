"""Fan-out from the orchestrator to the clients watching it.

Adapter layer, asyncio only. :class:`NoticeHub` carries change notices to
every open notice stream; each subscriber holds a pending set per kind, so a
slow reader costs memory bounded by the number of entities, never by a queue.
"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

from .notices import Notices, merge_notices

#: How long a notice waits for company before it is flushed.
COALESCE_SECS = 0.05


class NoticeSubscriber:
    """One client's view of the notices it has not read yet."""

    def __init__(self, coalesce: float) -> None:
        self.pending: Notices = {}
        self._coalesce = coalesce
        self._wake = asyncio.Event()

    def add(self, notices: Notices) -> None:
        if not notices:
            return
        merge_notices(self.pending, notices)
        self._wake.set()

    async def next(self) -> Notices:
        """The pending notices, once some exist and the coalesce window has passed.

        The window lets a burst of events from one poll land as one notice
        per kind, so a client refetches a list once rather than per row.
        """
        await self._wake.wait()
        await asyncio.sleep(self._coalesce)
        batch, self.pending = self.pending, {}
        self._wake.clear()
        return batch


class NoticeHub:
    """Every open notice stream, and what each still has to read."""

    def __init__(self, coalesce: float = COALESCE_SECS) -> None:
        self._coalesce = coalesce
        self._subscribers: set[NoticeSubscriber] = set()
        #: How many non-empty notice batches have been published, so a test
        #: can check "nothing was published" without waiting for silence.
        self.published = 0

    def notify(self, notices: Notices) -> None:
        if not notices:
            return
        self.published += 1
        for subscriber in self._subscribers:
            subscriber.add(notices)

    @contextmanager
    def subscribe(self) -> Iterator[NoticeSubscriber]:
        """A subscriber that hears every notice published inside the block."""
        subscriber = NoticeSubscriber(self._coalesce)
        self._subscribers.add(subscriber)
        try:
            yield subscriber
        finally:
            self._subscribers.discard(subscriber)
