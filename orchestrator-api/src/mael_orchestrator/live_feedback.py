"""The orchestrator as a **Live feedback provider**: which worktrees show their jig.

Adapter layer, asyncio only. Each live feedback socket subscribes for its
worktree, and hears each change to it. The protocol is documented in
``docs/dev/orchestrator-ui.md``, "The jig".
"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager


class LiveFeedbackHub:
    """The worktrees whose jig is shown, and the sockets that watch each."""

    def __init__(self) -> None:
        self._shown: set[str] = set()
        self._subscribers: dict[str, set[asyncio.Queue[bool]]] = {}

    def visible(self, worktree_id: str) -> bool:
        return worktree_id in self._shown

    def set(self, worktree_id: str, visible: bool) -> None:
        """Show or hide the worktree's jig. Only a change reaches the sockets."""
        if self.visible(worktree_id) == visible:
            return
        if visible:
            self._shown.add(worktree_id)
        else:
            self._shown.discard(worktree_id)
        for queue in self._subscribers.get(worktree_id, ()):
            queue.put_nowait(visible)

    @contextmanager
    def subscribe(self, worktree_id: str) -> Iterator[asyncio.Queue[bool]]:
        """A queue of each state the worktree's jig takes inside the block."""
        queue: asyncio.Queue[bool] = asyncio.Queue()
        self._subscribers.setdefault(worktree_id, set()).add(queue)
        try:
            yield queue
        finally:
            self._subscribers[worktree_id].discard(queue)
            if not self._subscribers[worktree_id]:
                del self._subscribers[worktree_id]
