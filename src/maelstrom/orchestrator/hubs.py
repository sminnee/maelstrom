"""Fan-out from the orchestrator to the clients watching it.

Adapter layer, asyncio only. :class:`NoticeHub` carries change notices to
every open notice stream; each subscriber holds a pending set per kind, so a
slow reader costs memory bounded by the number of entities, never by a queue.
:class:`TranscriptHub` carries one agent's transcript frames to every socket
open on it; a reader that falls a queue behind is told so and closed, and
resumes from its seq.
"""

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from .notices import Notices, merge_notices
from .transcript_log import TranscriptFrame

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


#: How many frames a transcript socket may fall behind before it is closed.
WS_QUEUE_LIMIT = 500


class Lagging:
    """Put on a lagging subscriber's queue in place of the frames it lost."""


#: The one instance; a reader checks with ``isinstance``.
LAGGING = Lagging()


class TranscriptSubscriber:
    """One socket's unread frames for one agent."""

    def __init__(self, limit: int) -> None:
        self.queue: asyncio.Queue[TranscriptFrame | Lagging] = asyncio.Queue(
            maxsize=limit
        )
        self.lagging = False

    def push(self, frame: TranscriptFrame) -> None:
        if self.lagging:
            return
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            # The reader is behind by a whole queue. It resumes from its seq,
            # so nothing is lost by dropping the rest and saying so.
            self.lagging = True
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(LAGGING)

    async def next(self) -> TranscriptFrame | Lagging:
        return await self.queue.get()


class TranscriptHub:
    """Every open transcript socket, per agent.

    ``on_idle`` is called with the agent id when its last socket closes, so
    the server can stop following a stream nobody is reading.
    """

    def __init__(
        self,
        queue_limit: int = WS_QUEUE_LIMIT,
        on_idle: Callable[[str], None] | None = None,
    ) -> None:
        self._limit = queue_limit
        self._subscribers: dict[str, set[TranscriptSubscriber]] = {}
        self.on_idle = on_idle

    def push(self, agent_id: str, frames: list[TranscriptFrame]) -> None:
        for subscriber in self._subscribers.get(agent_id, ()):
            for frame in frames:
                subscriber.push(frame)

    @contextmanager
    def subscribe(self, agent_id: str) -> Iterator[TranscriptSubscriber]:
        """A subscriber that hears every frame pushed for ``agent_id`` inside the block."""
        subscriber = TranscriptSubscriber(self._limit)
        self._subscribers.setdefault(agent_id, set()).add(subscriber)
        try:
            yield subscriber
        finally:
            self._subscribers[agent_id].discard(subscriber)
            if not self._subscribers[agent_id] and self.on_idle is not None:
                self.on_idle(agent_id)

    def count(self, agent_id: str) -> int:
        return len(self._subscribers.get(agent_id, ()))
