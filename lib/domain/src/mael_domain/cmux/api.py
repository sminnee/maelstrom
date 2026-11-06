"""The typed cmux API, between the transport and the policy.

See docs/dev/cmux.md.
"""

import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from .client import CmuxClient

SurfaceType = Literal["terminal", "browser"]

# Global flags that make cmux reply in JSON with both refs and UUIDs. A ref is a
# ``--workspace`` / ``--surface`` handle; a UUID is what a deep link names.
_JSON_BOTH = ("--json", "--id-format", "both")


@dataclass(frozen=True)
class Workspace:
    ref: str
    id: str
    title: str


@dataclass(frozen=True)
class Surface:
    ref: str
    id: str
    type: str
    title: str
    pane_ref: str
    pane_id: str


class CmuxApi(Protocol):
    """The cmux commands maelstrom uses."""

    def list_workspaces(self) -> list[Workspace]: ...

    def list_surfaces(self, workspace: str) -> list[Surface]:
        """Surfaces by pane, then by tab order in the pane."""
        ...

    def new_workspace(self, command: str) -> str | None:
        """Make a workspace whose one terminal runs ``command``. Its ref."""
        ...

    def rename_workspace(self, workspace: str, title: str) -> bool: ...

    def rename_tab(self, surface: str, title: str) -> bool: ...

    def close_workspace(self, workspace: str) -> bool: ...


def _entries(reply: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    """The objects in ``reply[key]``. Anything of another shape is dropped."""
    entries = (reply or {}).get(key)
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict)]


def _text(entry: dict[str, Any], key: str) -> str | None:
    value = entry.get(key)
    return value if isinstance(value, str) and value else None


class CliCmuxApi(CmuxApi):
    """The real :class:`CmuxApi`: argv in, parsed records out."""

    def __init__(self, client: CmuxClient) -> None:
        self._client = client

    # === reads ===

    def list_workspaces(self) -> list[Workspace]:
        reply = self._client.run(*_JSON_BOTH, "list-workspaces").json()
        workspaces = []
        for entry in _entries(reply, "workspaces"):
            ref, id_ = _text(entry, "ref"), _text(entry, "id")
            if ref and id_:
                workspaces.append(Workspace(ref, id_, _text(entry, "title") or ""))
        return workspaces

    def list_surfaces(self, workspace: str) -> list[Surface]:
        args = [*_JSON_BOTH, "list-panels", "--workspace", workspace]
        # ``index`` is the surface's place in the workspace: by pane, then by tab.
        surfaces: list[tuple[int, Surface]] = []
        for entry in _entries(self._client.run(*args).json(), "surfaces"):
            ref, id_, index = (
                _text(entry, "ref"),
                _text(entry, "id"),
                entry.get("index"),
            )
            pane_ref, pane_id = _text(entry, "pane_ref"), _text(entry, "pane_id")
            if ref and id_ and pane_ref and pane_id and isinstance(index, int):
                surface = Surface(
                    ref,
                    id_,
                    _text(entry, "type") or "",
                    _text(entry, "title") or "",
                    pane_ref,
                    pane_id,
                )
                surfaces.append((index, surface))
        return [surface for _, surface in sorted(surfaces, key=lambda s: s[0])]

    # === writes ===

    def new_workspace(self, command: str) -> str | None:
        result = self._client.run("new-workspace", "--command", command)
        return result.text or None

    def rename_workspace(self, workspace: str, title: str) -> bool:
        return self._client.run("rename-workspace", "--workspace", workspace, title).ok

    def rename_tab(self, surface: str, title: str) -> bool:
        return self._client.run("rename-tab", "--surface", surface, title).ok

    def close_workspace(self, workspace: str) -> bool:
        return self._client.run("close-workspace", "--workspace", workspace).ok


# --- the fake ---


@dataclass
class _FakeSurface:
    ref: str
    id: str
    type: SurfaceType
    title: str = ""
    url: str | None = None


@dataclass
class _FakePane:
    ref: str
    id: str
    surfaces: list[_FakeSurface] = field(default_factory=list)


@dataclass
class _FakeWorkspace:
    ref: str
    id: str
    title: str
    panes: list[_FakePane] = field(default_factory=list)


# A tab for :meth:`FakeCmux.with_workspace`: a terminal's title, or a
# ``("browser", url)`` pair.
FakeTab = str | tuple[Literal["browser"], str]


class FakeCmux(CmuxApi):
    """A stateful in-memory :class:`CmuxApi`. See docs/dev/cmux.md, "The fake"."""

    def __init__(self) -> None:
        self._workspaces: list[_FakeWorkspace] = []
        self._counters = {
            k: itertools.count(1) for k in ("workspace", "pane", "surface")
        }
        #: The command each new workspace's terminal ran, in order.
        self.commands: list[str] = []

    # === builders and inspection, for tests ===

    def with_workspace(
        self,
        title: str,
        panes: Sequence[Sequence[FakeTab]] = (("Terminal",),),
    ) -> "FakeCmux":
        """Add a workspace ``title`` with ``panes`` of tabs. Returns ``self``."""
        workspace = self._make_workspace(title)
        for tabs in panes:
            pane = self._make_pane()
            for tab in tabs:
                if isinstance(tab, str):
                    pane.surfaces.append(self._make_surface("terminal", title=tab))
                else:
                    pane.surfaces.append(self._make_surface("browser", url=tab[1]))
            workspace.panes.append(pane)
        return self

    def tabs(self, title: str) -> list[list[str]]:
        """Each pane's tab titles (a browser's URL), left to right."""
        workspace = self._named(title)
        if workspace is None:
            return []
        return [
            [(s.url or "") if s.type == "browser" else s.title for s in p.surfaces]
            for p in workspace.panes
        ]

    def pane_link(self, title: str, pane: int) -> str:
        """The ``cmux://`` link to pane ``pane`` of workspace ``title``."""
        workspace = self._named(title)
        assert workspace is not None, f"no workspace {title!r}"
        return f"cmux://workspace/{workspace.id}/pane/{workspace.panes[pane].id}"

    # === reads ===

    def list_workspaces(self) -> list[Workspace]:
        return [Workspace(w.ref, w.id, w.title) for w in self._workspaces]

    def list_surfaces(self, workspace: str) -> list[Surface]:
        found = self._resolve(workspace)
        if found is None:
            return []
        return [
            Surface(s.ref, s.id, s.type, s.title, p.ref, p.id)
            for p in found.panes
            for s in p.surfaces
        ]

    # === writes ===

    def new_workspace(self, command: str) -> str | None:
        workspace = self._make_workspace("")
        pane = self._make_pane()
        pane.surfaces.append(self._make_surface("terminal"))
        workspace.panes.append(pane)
        self.commands.append(command)
        return workspace.ref

    def rename_workspace(self, workspace: str, title: str) -> bool:
        found = self._resolve(workspace)
        if found is None:
            return False
        found.title = title
        return True

    def rename_tab(self, surface: str, title: str) -> bool:
        found = next(
            (
                s
                for w in self._workspaces
                for p in w.panes
                for s in p.surfaces
                if s.ref == surface
            ),
            None,
        )
        if found is None:
            return False
        found.title = title
        return True

    def close_workspace(self, workspace: str) -> bool:
        found = self._resolve(workspace)
        if found is None:
            return False
        self._workspaces.remove(found)
        return True

    # === private ===

    def _named(self, title: str) -> _FakeWorkspace | None:
        return next((w for w in self._workspaces if w.title == title), None)

    def _resolve(self, ref: str) -> _FakeWorkspace | None:
        return next((w for w in self._workspaces if w.ref == ref), None)

    def _make_workspace(self, title: str) -> _FakeWorkspace:
        n = next(self._counters["workspace"])
        workspace = _FakeWorkspace(f"workspace:{n}", f"WORKSPACE-{n}", title)
        self._workspaces.append(workspace)
        return workspace

    def _make_pane(self) -> _FakePane:
        n = next(self._counters["pane"])
        return _FakePane(f"pane:{n}", f"PANE-{n}")

    def _make_surface(
        self, type: SurfaceType, *, title: str = "", url: str | None = None
    ) -> _FakeSurface:
        n = next(self._counters["surface"])
        return _FakeSurface(f"surface:{n}", f"SURFACE-{n}", type, title, url)
