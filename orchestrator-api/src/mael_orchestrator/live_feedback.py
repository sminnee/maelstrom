"""The orchestrator as a **Live feedback provider**: each dev server's params.

Adapter layer, asyncio only. Each live feedback socket subscribes for its
worktree and service, and hears each change to that service's params. The
protocol is documented in ``docs/dev/live-feedback.md``.
"""

import asyncio
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from mael_domain.protocol import LiveFeedbackParam, LiveFeedbackService

#: One dev server of one worktree: what the hub keys its state by.
Key = tuple[str, str]

#: The param types the hub takes. A declaration of another type is ignored.
PARAM_TYPES = ("boolean",)

#: A param name the ``<live-feedback>`` marker can carry as an attribute.
#: ``service`` is the marker's own, so no param takes it.
_PARAM_NAME = re.compile(r"\w+")
RESERVED_NAMES = ("service",)


class LiveFeedbackHub:
    """The params each dev server exposes, their values, and the sockets that hear them.

    A declaration lasts while its service has a socket open. A value outlives
    the socket, so a dev server that restarts gets its values back.

    ``on_change`` hears the worktree whose :meth:`state` moved: a declaration,
    a value, or the last socket of a service closing.
    """

    def __init__(self, on_change: Callable[[str], None]) -> None:
        self._subscribers: dict[Key, set[asyncio.Queue[dict[str, bool]]]] = {}
        self._declared: dict[Key, dict[str, dict[str, str]]] = {}
        self._values: dict[Key, dict[str, bool]] = {}
        self._on_change = on_change

    @contextmanager
    def subscribe(
        self, worktree_id: str, service: str
    ) -> Iterator[asyncio.Queue[dict[str, bool]]]:
        """A queue of the ``setParams`` values the service hears inside the block."""
        key = (worktree_id, service)
        queue: asyncio.Queue[dict[str, bool]] = asyncio.Queue()
        self._subscribers.setdefault(key, set()).add(queue)
        try:
            yield queue
        finally:
            self._subscribers[key].discard(queue)
            if not self._subscribers[key]:
                del self._subscribers[key]
                if self._declared.pop(key, None) is not None:
                    self._on_change(worktree_id)

    def expose(self, worktree_id: str, service: str, params: Any) -> None:
        """Take the service's newest declaration, and send it the stored values."""
        key = (worktree_id, service)
        if key not in self._subscribers:
            return
        self._declared[key] = _declarations(params)
        self._push(key, self.values(worktree_id, service))
        self._on_change(worktree_id)

    def declared(self, worktree_id: str, service: str) -> dict[str, dict[str, str]]:
        """The params the service exposes now, by name. Empty when it is not connected."""
        return self._declared.get((worktree_id, service), {})

    def values(self, worktree_id: str, service: str) -> dict[str, bool]:
        """The value of each param the service exposes. A param never set is off."""
        stored = self._values.get((worktree_id, service), {})
        return {
            name: stored.get(name, False)
            for name in self.declared(worktree_id, service)
        }

    def set(self, worktree_id: str, service: str, values: dict[str, bool]) -> None:
        """Set the service's params. A param it does not expose is ignored.

        Only a change reaches the sockets and ``on_change``.
        """
        key = (worktree_id, service)
        current = self.values(worktree_id, service)
        changed = {
            name: value
            for name, value in values.items()
            if name in current and current[name] != value
        }
        if not changed:
            return
        self._values.setdefault(key, {}).update(changed)
        self._push(key, changed)
        self._on_change(worktree_id)

    def revert(
        self,
        worktree_id: str,
        service: str,
        before: dict[str, bool],
        after: dict[str, bool],
    ) -> None:
        """Undo a :meth:`set` of ``after``: each param still at that value gets ``before`` back.

        The stored value is written whether or not the service is connected, so
        a refused change does not come back with the next ``exposeParams``. A
        param changed again since is left alone.
        """
        key = (worktree_id, service)
        stored = self._values.get(key, {})
        undone = {
            name: before[name]
            for name, value in after.items()
            if stored.get(name, False) == value
        }
        if not undone:
            return
        self._values.setdefault(key, {}).update(undone)
        declared = self.declared(worktree_id, service)
        heard = {name: value for name, value in undone.items() if name in declared}
        if heard:
            self._push(key, heard)
            self._on_change(worktree_id)

    def set_everywhere(self, worktree_id: str, name: str, value: bool) -> None:
        """Set ``name`` on each connected service of the worktree that exposes it."""
        for wt, service in list(self._declared):
            if wt == worktree_id:
                self.set(worktree_id, service, {name: value})

    def clear(self, worktree_id: str) -> None:
        """Turn every param of the worktree off, on a connected service or not."""
        for key in [key for key in self._values if key[0] == worktree_id]:
            self.set(*key, dict.fromkeys(self._values[key], False))
            # A disconnected service heard nothing above; its values go too.
            del self._values[key]

    def state(self, worktree_id: str) -> list[LiveFeedbackService]:
        """The worktree's connected services and their params, as the world carries them."""
        return [
            self._service_state(worktree_id, service)
            for wt, service in sorted(self._declared)
            if wt == worktree_id
        ]

    def _service_state(self, worktree_id: str, service: str) -> LiveFeedbackService:
        values = self.values(worktree_id, service)
        return {
            "service": service,
            "params": {
                name: _param(declaration, values[name])
                for name, declaration in self.declared(worktree_id, service).items()
            },
        }

    def _push(self, key: Key, values: dict[str, bool]) -> None:
        for queue in self._subscribers.get(key, ()):
            queue.put_nowait(values)


def _declarations(params: Any) -> dict[str, dict[str, str]]:
    """The params of an ``exposeParams`` frame the hub takes, by name."""
    if not isinstance(params, dict):
        return {}
    taken: dict[str, dict[str, str]] = {}
    for name, declaration in params.items():
        if (
            not _PARAM_NAME.fullmatch(name)
            or name in RESERVED_NAMES
            or not isinstance(declaration, dict)
            or declaration.get("type") not in PARAM_TYPES
        ):
            continue
        label = declaration.get("label")
        description = declaration.get("description")
        taken[name] = {
            "type": declaration["type"],
            "label": label if isinstance(label, str) and label else name,
            "description": description if isinstance(description, str) else "",
        }
    return taken


def _param(declaration: dict[str, str], value: bool) -> LiveFeedbackParam:
    return {
        "label": declaration["label"],
        "type": declaration["type"],
        "description": declaration["description"],
        "value": value,
    }
