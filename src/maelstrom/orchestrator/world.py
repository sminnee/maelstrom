"""The world the orchestrator server holds, and the one way it changes.

The world is only ever reached by applying events through
:func:`~maelstrom.orchestrator.protocol.apply_event`; nothing mutates it
directly. What the events amount to travels to clients as change notices
(:mod:`.notices`) and transcript frames (:mod:`.transcript_log`), never as
the events themselves.
"""

from .protocol import ClientState, ServerEvent, World, apply_event, initial_client_state


class WorldState:
    """The server's world: the tables, and ``apply`` as their only writer."""

    def __init__(self) -> None:
        self._state: ClientState = initial_client_state()

    @property
    def state(self) -> ClientState:
        return self._state

    @property
    def world(self) -> World:
        return self._state["world"]

    def apply(self, events: list[ServerEvent]) -> None:
        """Apply ``events`` in order. Synchronous: nothing observes a half-applied batch."""
        for event in events:
            self._state = apply_event(self._state, event)
