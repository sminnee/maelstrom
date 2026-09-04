"""The seq-stamped event log the orchestrator server serves clients from.

A port of ``web/src/fake-backend/store.ts``. The world is only ever reached
by applying events through
:func:`~maelstrom.orchestrator.protocol.apply_event`; nothing mutates it
directly.
"""

from collections import deque

from .protocol import (
    ClientState,
    EventFrame,
    ServerEvent,
    apply_event,
    initial_client_state,
)

#: How many frames the log keeps for ``replay_from``. Older resumes get a snapshot.
RING_SIZE = 5000


class EventLog:
    """The server's only state: the world and the stamped log of how it got there."""

    def __init__(self, ring_size: int = RING_SIZE) -> None:
        self._state: ClientState = initial_client_state()
        self._seq = 0
        self._log: deque[EventFrame] = deque(maxlen=ring_size)

    @property
    def state(self) -> ClientState:
        return self._state

    @property
    def seq(self) -> int:
        return self._seq

    def append(self, events: list[ServerEvent], ts: str) -> list[EventFrame]:
        """Apply ``events`` in order, stamp them, keep them, and return the frames."""
        frames: list[EventFrame] = []
        for event in events:
            self._seq += 1
            self._state = apply_event(self._state, event, self._seq)
            frame: EventFrame = {"seq": self._seq, "ts": ts, "event": event}
            self._log.append(frame)
            frames.append(frame)
        return frames

    def replay_from(self, from_seq: int) -> list[EventFrame] | None:
        """Frames with seq > ``from_seq``, or ``None`` when the ring no longer holds them.

        ``from_seq`` is the last seq the client applied, so the ring must still
        hold ``from_seq + 1`` for the replay to be gapless.
        """
        if not self._log:
            return None
        oldest = self._log[0]["seq"]
        if from_seq < oldest - 1:
            return None
        return [frame for frame in self._log if frame["seq"] > from_seq]

    def snapshot_frame(self, ts: str) -> EventFrame:
        """The world as it is now, stamped with the current seq.

        Carries no transcripts: the server keeps none.
        """
        return {
            "seq": self._seq,
            "ts": ts,
            "event": {"type": "snapshot", "world": self._state["world"]},
        }
