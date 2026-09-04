"""One agent's transcript as the server keeps it: the items, and a ring of frames.

Pure. The server appends the ``transcript.*`` events the normaliser makes,
each stamped with the agent's own seq; a client that comes back with the
last seq it applied gets the frames after it while the ring still holds
them, and a snapshot otherwise. See ``docs/dev/orchestrator-server.md``,
"Transcript streams".
"""

from collections import deque
from collections.abc import Callable
from typing import TypedDict

from .protocol import ServerEvent, TranscriptItem

#: How many frames a transcript keeps for ``replay_from``.
TRANSCRIPT_RING = 2000
#: How many items a transcript keeps. Past it the oldest go, and the snapshot
#: says so, the way it does when the host's own window rolled.
TRANSCRIPT_ITEMS = 5000


class TranscriptFrame(TypedDict):
    seq: int
    event: ServerEvent


class TranscriptSnapshot(TypedDict):
    items: list[TranscriptItem]
    truncatedBefore: bool
    seq: int


class TranscriptLog:
    """The items so far, the seq of the last frame, and the frames a resume can replay."""

    def __init__(
        self, ring: int = TRANSCRIPT_RING, max_items: int = TRANSCRIPT_ITEMS
    ) -> None:
        self.seq = 0
        self.items: list[TranscriptItem] = []
        self.truncated_before = False
        self._max_items = max_items
        self._frames: deque[TranscriptFrame] = deque(maxlen=ring)

    def append(self, event: ServerEvent) -> TranscriptFrame:
        """Apply one transcript event, stamp it, keep it, and return its frame."""
        kind = event["type"]
        if kind == "transcript.append":
            self.items.append(dict(event["item"]))
            if len(self.items) > self._max_items:
                del self.items[: len(self.items) - self._max_items]
                self.truncated_before = True
        elif kind == "transcript.update":
            item_id = event["itemId"]
            self.items = [
                {**item, **event["patch"]} if item["id"] == item_id else item
                for item in self.items
            ]
        elif kind == "transcript.truncated":
            self.truncated_before = True
        else:
            raise ValueError(f"Not a transcript event: {event!r}")
        self.seq += 1
        frame: TranscriptFrame = {"seq": self.seq, "event": event}
        self._frames.append(frame)
        return frame

    def replay_from(self, from_seq: int) -> list[TranscriptFrame] | None:
        """Frames with seq > ``from_seq``, or ``None`` when a snapshot is needed.

        ``from_seq`` is the last seq the client applied, so the ring must
        still hold ``from_seq + 1`` for the replay to be gapless. A seq this
        log never reached is a cursor from another life, and gets a snapshot.
        """
        if from_seq > self.seq:
            return None
        if from_seq == self.seq:
            return []
        oldest = self._frames[0]["seq"] if self._frames else self.seq + 1
        if from_seq < oldest - 1:
            return None
        return [frame for frame in self._frames if frame["seq"] > from_seq]

    def snapshot(self) -> TranscriptSnapshot:
        return {
            "items": list(self.items),
            "truncatedBefore": self.truncated_before,
            "seq": self.seq,
        }

    def find(
        self, predicate: Callable[[TranscriptItem], bool]
    ) -> TranscriptItem | None:
        """The last item ``predicate`` accepts, or ``None``."""
        for item in reversed(self.items):
            if predicate(item):
                return item
        return None
