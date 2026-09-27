"""The typed cmux API, between the transport and the mechanics.

See docs/dev/cmux.md.
"""

import itertools
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from .client import CmuxClient

SurfaceType = Literal["terminal", "browser"]

# cmux splits inherit the source pane's *current* cwd. A shell that has not
# finished its first `cd` yet would give the new pane the wrong cwd, so a split
# waits this long first.
_SPLIT_SETTLE_SECONDS = 0.25

# Global flags that make cmux reply in JSON with both refs and UUIDs. A ref is a
# ``--workspace`` / ``--surface`` handle; a UUID is what a deep link names.
_JSON_BOTH = ("--json", "--id-format", "both")


@dataclass(frozen=True)
class Workspace:
    ref: str
    id: str
    title: str


@dataclass(frozen=True)
class Pane:
    ref: str
    id: str
    index: int
    selected_surface: str | None


@dataclass(frozen=True)
class Surface:
    ref: str
    id: str
    type: str
    title: str
    pane_ref: str
    pane_id: str


class CmuxApi(Protocol):
    """The cmux commands maelstrom uses. ``workspace=None`` is the caller's."""

    def list_workspaces(self) -> list[Workspace]: ...

    def list_panes(self, workspace: str | None) -> list[Pane]:
        """Panes left to right."""
        ...

    def list_surfaces(self, workspace: str | None) -> list[Surface]:
        """Surfaces by pane, then by tab order in the pane."""
        ...

    def browser_url(self, surface: str) -> str | None: ...

    def new_workspace(self, command: str) -> str | None:
        """Make a workspace whose one terminal runs ``command``. Its ref."""
        ...

    def rename_workspace(self, workspace: str, title: str) -> bool: ...

    def new_split(self, surface: str, direction: str, workspace: str | None) -> bool:
        """Split a pane with one terminal off ``surface``'s pane, without focus."""
        ...

    def new_surface(
        self,
        type: SurfaceType,
        pane: str | None,
        workspace: str | None,
        url: str | None = None,
    ) -> str | None:
        """Add a tab to ``pane`` (cmux's default pane when ``None``). Its ref."""
        ...

    def rename_tab(self, surface: str, title: str) -> bool: ...

    def send(self, surface: str | None, workspace: str | None, text: str) -> bool:
        """Type ``text`` into ``surface``, or the workspace's active surface."""
        ...

    def browser_goto(self, surface: str, url: str) -> bool: ...

    def close_surface(self, surface: str) -> bool: ...

    def close_workspace(self, workspace: str) -> bool: ...

    def select_workspace(self, workspace: str) -> bool: ...

    def focus_pane(self, pane: str, workspace: str | None) -> bool: ...

    def focus_surface(self, surface: str, workspace: str | None) -> bool: ...


def _workspace_arg(workspace: str | None) -> list[str]:
    return ["--workspace", workspace] if workspace else []


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

    def list_panes(self, workspace: str | None) -> list[Pane]:
        args = [*_JSON_BOTH, "list-panes", *_workspace_arg(workspace)]
        panes = []
        for entry in _entries(self._client.run(*args).json(), "panes"):
            ref, id_, index = (
                _text(entry, "ref"),
                _text(entry, "id"),
                entry.get("index"),
            )
            if ref and id_ and isinstance(index, int):
                refs = entry.get("surface_refs")
                first = refs[0] if isinstance(refs, list) and refs else None
                selected = _text(entry, "selected_surface_ref") or first
                panes.append(Pane(ref, id_, index, selected))
        return sorted(panes, key=lambda p: p.index)

    def list_surfaces(self, workspace: str | None) -> list[Surface]:
        args = [*_JSON_BOTH, "list-panels", *_workspace_arg(workspace)]
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

    def browser_url(self, surface: str) -> str | None:
        return self._client.run("browser", "get-url", "--surface", surface).raw or None

    # === writes ===

    def new_workspace(self, command: str) -> str | None:
        result = self._client.run("new-workspace", "--command", command)
        return result.text or None

    def rename_workspace(self, workspace: str, title: str) -> bool:
        return self._client.run("rename-workspace", "--workspace", workspace, title).ok

    def new_split(self, surface: str, direction: str, workspace: str | None) -> bool:
        time.sleep(_SPLIT_SETTLE_SECONDS)
        args = ["new-split", direction, "--surface", surface]
        return self._client.run(*args, *_workspace_arg(workspace)).ok

    def new_surface(
        self,
        type: SurfaceType,
        pane: str | None,
        workspace: str | None,
        url: str | None = None,
    ) -> str | None:
        # new-surface replies "OK surface:N pane:N workspace:N"; only the leading
        # surface ref is a valid --surface handle.
        args = ["new-surface", "--type", type]
        if pane:
            args += ["--pane", pane]
        if url is not None:
            args += ["--url", url]
        return self._client.run(*args, *_workspace_arg(workspace)).ref("surface")

    def rename_tab(self, surface: str, title: str) -> bool:
        return self._client.run("rename-tab", "--surface", surface, title).ok

    def send(self, surface: str | None, workspace: str | None, text: str) -> bool:
        # ``send`` defaults --workspace to the caller's $CMUX_WORKSPACE_ID. A
        # surface in another workspace needs its own, or cmux cannot find it.
        args = ["send", *(["--surface", surface] if surface else [])]
        return self._client.run(*args, *_workspace_arg(workspace), "--", text).ok

    def browser_goto(self, surface: str, url: str) -> bool:
        return self._client.run("browser", "--surface", surface, "goto", url).ok

    def close_surface(self, surface: str) -> bool:
        return self._client.run("close-surface", "--surface", surface).ok

    def close_workspace(self, workspace: str) -> bool:
        return self._client.run("close-workspace", "--workspace", workspace).ok

    def select_workspace(self, workspace: str) -> bool:
        return self._client.run("select-workspace", "--workspace", workspace).ok

    def focus_pane(self, pane: str, workspace: str | None) -> bool:
        args = ["focus-pane", "--pane", pane, *_workspace_arg(workspace)]
        return self._client.run(*args).ok

    def focus_surface(self, surface: str, workspace: str | None) -> bool:
        # Surfaces are panels to cmux.
        args = ["focus-panel", "--panel", surface, *_workspace_arg(workspace)]
        return self._client.run(*args).ok


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
    selected: str | None = None


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
        self._current: str | None = None
        self._counters = {
            k: itertools.count(1) for k in ("workspace", "pane", "surface")
        }
        self.sent: list[tuple[str, str]] = []
        self.focused: list[str] = []

    # === builders and inspection, for tests ===

    def with_workspace(
        self,
        title: str,
        panes: Sequence[Sequence[FakeTab]] = (("Terminal",),),
        *,
        current: bool = False,
    ) -> "FakeCmux":
        """Add a workspace ``title`` with ``panes`` of tabs. Returns ``self``."""
        workspace = self._make_workspace(title)
        for tabs in panes:
            pane = self._make_pane()
            for tab in tabs:
                if isinstance(tab, str):
                    self._add(pane, self._make_surface("terminal", title=tab))
                else:
                    self._add(pane, self._make_surface("browser", url=tab[1]))
            workspace.panes.append(pane)
        if current:
            self._current = workspace.ref
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

    def workspace_ref(self, title: str) -> str | None:
        """The ref of the first workspace named ``title``."""
        workspace = self._named(title)
        return workspace.ref if workspace else None

    def surface_ref(self, title: str, pane: int, tab: int = 0) -> str:
        """The ref of tab ``tab`` in pane ``pane`` of workspace ``title``."""
        workspace = self._named(title)
        assert workspace is not None, f"no workspace {title!r}"
        return workspace.panes[pane].surfaces[tab].ref

    def pane_link(self, title: str, pane: int) -> str:
        """The ``cmux://`` link to pane ``pane`` of workspace ``title``."""
        workspace = self._named(title)
        assert workspace is not None, f"no workspace {title!r}"
        return f"cmux://workspace/{workspace.id}/pane/{workspace.panes[pane].id}"

    def texts(self) -> list[str]:
        """Each text sent into a terminal, in order."""
        return [text for _, text in self.sent]

    # === reads ===

    def list_workspaces(self) -> list[Workspace]:
        return [Workspace(w.ref, w.id, w.title) for w in self._workspaces]

    def list_panes(self, workspace: str | None) -> list[Pane]:
        found = self._resolve(workspace)
        if found is None:
            return []
        return [Pane(p.ref, p.id, i, p.selected) for i, p in enumerate(found.panes)]

    def list_surfaces(self, workspace: str | None) -> list[Surface]:
        found = self._resolve(workspace)
        if found is None:
            return []
        return [
            Surface(s.ref, s.id, s.type, s.title, p.ref, p.id)
            for p in found.panes
            for s in p.surfaces
        ]

    def browser_url(self, surface: str) -> str | None:
        found = self._surface(surface)
        return found.url if found else None

    # === writes ===

    def new_workspace(self, command: str) -> str | None:
        workspace = self._make_workspace("")
        pane = self._make_pane()
        workspace.panes.append(pane)
        terminal = self._add(pane, self._make_surface("terminal"))
        if command:
            self.sent.append((terminal.ref, f"{command}\n"))
        return workspace.ref

    def rename_workspace(self, workspace: str, title: str) -> bool:
        found = self._resolve(workspace)
        if found is None:
            return False
        found.title = title
        return True

    def new_split(self, surface: str, direction: str, workspace: str | None) -> bool:
        found = self._resolve(workspace)
        source = self._pane_of(found, surface) if found else None
        if found is None or source is None:
            return False
        pane = self._make_pane()
        self._add(pane, self._make_surface("terminal"))
        found.panes.insert(found.panes.index(source) + 1, pane)
        return True

    def new_surface(
        self,
        type: SurfaceType,
        pane: str | None,
        workspace: str | None,
        url: str | None = None,
    ) -> str | None:
        found = self._resolve(workspace)
        if found is None or not found.panes:
            return None
        target = next((p for p in found.panes if p.ref == pane), None)
        if pane is None:
            target = found.panes[0]
        if target is None:
            return None
        return self._add(target, self._make_surface(type, url=url)).ref

    def rename_tab(self, surface: str, title: str) -> bool:
        found = self._surface(surface)
        if found is None:
            return False
        found.title = title
        return True

    def send(self, surface: str | None, workspace: str | None, text: str) -> bool:
        found = self._resolve(workspace)
        if found is None or not found.panes:
            return False
        if surface is None:
            surface = found.panes[0].selected
        # Like real cmux, a surface outside the named workspace is not found.
        pane = self._pane_of(found, surface) if surface else None
        target = self._surface(surface) if pane and surface else None
        if target is None or target.type != "terminal":
            return False
        self.sent.append((target.ref, text))
        return True

    def browser_goto(self, surface: str, url: str) -> bool:
        found = self._surface(surface)
        if found is None or found.type != "browser":
            return False
        found.url = url
        return True

    def close_surface(self, surface: str) -> bool:
        for workspace, pane, found in self._all_surfaces():
            if found.ref == surface:
                pane.surfaces.remove(found)
                if not pane.surfaces:
                    workspace.panes.remove(pane)
                elif pane.selected == surface:
                    pane.selected = pane.surfaces[-1].ref
                return True
        return False

    def close_workspace(self, workspace: str) -> bool:
        found = self._resolve(workspace)
        if found is None:
            return False
        self._workspaces.remove(found)
        return True

    def select_workspace(self, workspace: str) -> bool:
        return self._focus(workspace, self._resolve(workspace) is not None)

    def focus_pane(self, pane: str, workspace: str | None) -> bool:
        found = self._resolve(workspace)
        present = found is not None and any(p.ref == pane for p in found.panes)
        return self._focus(pane, present)

    def focus_surface(self, surface: str, workspace: str | None) -> bool:
        found = self._resolve(workspace)
        return self._focus(surface, bool(found and self._pane_of(found, surface)))

    # === private ===

    def _named(self, title: str) -> _FakeWorkspace | None:
        return next((w for w in self._workspaces if w.title == title), None)

    def _surface(self, ref: str) -> _FakeSurface | None:
        return next((s for _, _, s in self._all_surfaces() if s.ref == ref), None)

    def _resolve(self, workspace: str | None) -> _FakeWorkspace | None:
        ref = workspace or self._current
        return next((w for w in self._workspaces if w.ref == ref), None)

    def _make_workspace(self, title: str) -> _FakeWorkspace:
        n = next(self._counters["workspace"])
        workspace = _FakeWorkspace(f"workspace:{n}", f"WORKSPACE-{n}", title)
        self._workspaces.append(workspace)
        if self._current is None:
            self._current = workspace.ref
        return workspace

    def _make_pane(self) -> _FakePane:
        n = next(self._counters["pane"])
        return _FakePane(f"pane:{n}", f"PANE-{n}")

    def _make_surface(
        self, type: SurfaceType, *, title: str = "", url: str | None = None
    ) -> _FakeSurface:
        n = next(self._counters["surface"])
        return _FakeSurface(f"surface:{n}", f"SURFACE-{n}", type, title, url)

    @staticmethod
    def _add(pane: _FakePane, surface: _FakeSurface) -> _FakeSurface:
        pane.surfaces.append(surface)
        pane.selected = surface.ref
        return surface

    @staticmethod
    def _pane_of(workspace: _FakeWorkspace, surface: str) -> _FakePane | None:
        return next(
            (p for p in workspace.panes if any(s.ref == surface for s in p.surfaces)),
            None,
        )

    def _all_surfaces(self):
        for workspace in self._workspaces:
            for pane in workspace.panes:
                for surface in pane.surfaces:
                    yield workspace, pane, surface

    def _focus(self, ref: str, present: bool) -> bool:
        if present:
            self.focused.append(ref)
        return present
