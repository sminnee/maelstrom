"""Layout / domain layer for cmux integration.

Pure cmux mechanics over an injected :class:`~mael_domain.cmux.api.CmuxApi` —
**no maelstrom concepts** (no ``{project}-{worktree}`` naming, no Claude/install
knowledge, no "pane 2 is the browser" convention; that all lives in the policy
layer, ``mael_layout.py``).

The public surface is :class:`CmuxLayout`, which performs **partial, idempotent
reconciliation via discrete assertion verbs**. Each verb asserts a constraint
about a *subset* of one workspace, computes the delta for just that subset,
applies it, and is a no-op when the constraint already holds. Everything not
named by a verb is left untouched — so callers never describe the whole tree and
the layout never gets "weird" when the user has opened other things.

All verbs are non-fatal: they return a ref / bool and never raise.
"""

from dataclasses import dataclass

from .api import CmuxApi, Surface, Workspace

# --- value objects: inert specs (write side; no I/O, no maelstrom concepts) ---


@dataclass(frozen=True)
class TerminalTab:
    """A desired terminal tab: title, optional starting cwd, optional command.

    ``command`` is sent verbatim into the surface (shell-quoted by the caller).
    """

    title: str
    cwd: str | None = None
    command: str | None = None


@dataclass(frozen=True)
class BrowserTab:
    """A desired browser tab.

    ``match`` is the URL prefix used to recycle an existing browser by
    navigating it in place; it defaults to ``url``.
    """

    url: str
    match: str | None = None

    @property
    def match_prefix(self) -> str:
        return self.match if self.match is not None else self.url


class CmuxLayout:
    """Change operations over a single named cmux workspace.

    Bound to a workspace *name* plus an injected API. Two families of verb:

    - ``ensure_*`` — a **presence** assertion: guarantee *at least one* of the
      named entity matching the spec exists. Creates one if none does; no-op if
      one already does. Idempotent. (The dual ``ensure_absent_*`` guarantees
      *zero* — removes if present.)
    - ``add_*`` — **unconditionally add a new** entity (e.g. a second terminal
      tab). Not idempotent; this is the explicit "I want another one" operation.

    A cmux workspace always has at least one terminal surface, so creating a
    workspace (or splitting a new pane) *is* placing its first terminal: the new
    initial surface is reused for that terminal rather than left idle. The
    private methods below carry the cmux mechanics (focus-safety, surface refs).
    """

    def __init__(self, api: CmuxApi, workspace_name: str) -> None:
        self._api = api
        self._name = workspace_name

    # === workspace existence ===

    def workspace(self) -> Workspace | None:
        """The named workspace: the first one with this title."""
        return next(
            (w for w in self._api.list_workspaces() if w.title == self._name), None
        )

    def has_workspace(self) -> bool:
        """True if the named workspace exists."""
        return self._find_workspace() is not None

    def ensure_workspace(self, tab: TerminalTab) -> str | None:
        """Ensure the named workspace exists; create it if absent, else no-op.

        A workspace can't exist without a terminal, so creating one *is* placing
        its first terminal: the new workspace's initial surface is reused for
        ``tab`` (named, with its command sent in) rather than left idle. Returns
        the workspace ref, or ``None`` on failure / outside cmux.
        """
        existing = self._find_workspace()
        if existing is not None:
            return existing

        # Create running just the `cd` so the workspace's one initial terminal
        # lands in the worktree; the tab's command is then sent into that same
        # surface, and it is renamed to the tab title.
        cd = f"cd {tab.cwd}" if tab.cwd is not None else ""
        workspace_ref = self._api.new_workspace(cd)
        if workspace_ref is None:
            return None
        self._api.rename_workspace(workspace_ref, self._name)
        # A send with no surface targets the workspace's active (initial) one.
        if tab.command is not None:
            self._api.send(None, workspace_ref, f"{tab.command}\n")
        self._rename_pane_tab(workspace_ref, 0, tab.title)
        return workspace_ref

    # === terminal tabs by pane index ===

    def ensure_terminal(self, pane_index: int, tab: TerminalTab) -> str | None:
        """Ensure *at least one* terminal exists at pane ``pane_index``.

        If the pane already exists, it already has a terminal — no-op (returns
        that pane's surface). If it doesn't, split a new pane and reuse its
        initial surface for ``tab`` (send the command, rename) rather than adding
        a second tab. Returns the surface ref, or ``None``.
        """
        workspace_ref = self._find_workspace()
        if workspace_ref is None:
            return None

        pane_ref = self._pane_at_index(workspace_ref, pane_index)
        if pane_ref is not None:
            # Pane present → at least one terminal already exists here.
            return self._pane_surface(pane_ref, workspace_ref)

        # The split inherits the source shell's cwd, which may lag; the `cd` in
        # _run_tab below makes sure of it.
        new_pane = self._split_new_pane(workspace_ref)
        if new_pane is None:
            return None
        surface_ref = self._pane_surface(new_pane, workspace_ref)
        if surface_ref is None:
            return None
        self._run_tab(surface_ref, tab, workspace_ref)
        return surface_ref

    def add_terminal(self, pane_index: int, tab: TerminalTab) -> str | None:
        """Unconditionally add a *new* terminal tab to pane ``pane_index``.

        The explicit "I want another terminal" operation (not idempotent). The
        pane must already exist; returns the new surface ref, or ``None``. The
        new tab is brought to the front so the GUI shows it.
        """
        workspace_ref = self._find_workspace()
        if workspace_ref is None:
            return None
        pane_ref = self._pane_at_index(workspace_ref, pane_index)
        if pane_ref is None:
            return None

        surface_ref = self._add_terminal_tab(workspace_ref, pane_ref, tab.title)
        if surface_ref is None:
            return None
        self._run_tab(surface_ref, tab, workspace_ref)
        # Bring the (possibly background) workspace to the foreground, focus the
        # pane, then bring the new tab itself to the front — add_tab selects it in
        # the pane's data model, but the GUI keeps the previously-active tab
        # visible until we focus this surface (a panel) explicitly.
        self._api.select_workspace(workspace_ref)
        self._api.focus_pane(pane_ref, workspace_ref)
        self._api.focus_surface(surface_ref, workspace_ref)
        return surface_ref

    # === browser tabs in a designated browser pane ===

    def ensure_browser(self, pane_index: int, tab: BrowserTab) -> str | None:
        """Ensure *at least one* browser matching ``tab`` exists in pane ``pane_index``.

        Recycle rule (stated once, here): if a browser whose current URL prefix
        matches ``tab.match`` exists, navigate it in place; otherwise open a new
        background browser tab in pane ``pane_index`` (splitting a new pane off
        the rightmost surface, focus-safely, if pane ``pane_index`` doesn't exist
        yet). Returns the surface ref, or ``None``.
        """
        existing = self._find_browser_by_url(tab.match_prefix)
        if existing is not None:
            # Recycle in place — no close, no recreate, no focus capture.
            if self._api.browser_goto(existing.ref, tab.url):
                return existing.ref
            # Navigation failed; fall through to open a fresh tab.

        pane_ref = self._pane_at_index(None, pane_index)
        if pane_ref is not None:
            return self._api.new_surface("browser", pane_ref, None, url=tab.url)
        return self._open_browser_in_new_pane(tab.url)

    # === removal ===

    def ensure_absent_browser(self, url_prefix: str) -> bool:
        """Close the browser whose URL prefix-matches ``url_prefix``, if any."""
        browser = self._find_browser_by_url(url_prefix)
        if browser is None:
            return False
        return self._api.close_surface(browser.ref)

    # === teardown ===

    def close(self) -> bool:
        """Close the whole workspace. No-op (False) if it doesn't exist."""
        workspace_ref = self._find_workspace()
        if workspace_ref is None:
            return False
        return self._api.close_workspace(workspace_ref)

    # === private: terminal-tab mechanics ===

    def _run_tab(
        self,
        surface_ref: str,
        tab: TerminalTab,
        workspace_ref: str | None,
    ) -> None:
        """Send a tab's ``cd`` then command into an existing terminal surface."""
        if tab.cwd is not None:
            self._api.send(surface_ref, workspace_ref, f"cd {tab.cwd}\n")
        if tab.command is not None:
            self._api.send(surface_ref, workspace_ref, f"{tab.command}\n")

    def _rename_pane_tab(
        self,
        workspace_ref: str,
        pane_index: int,
        title: str,
    ) -> None:
        """Rename the (selected) surface of the pane at ``pane_index`` to ``title``."""
        if not title:
            return
        pane = self._pane_at_index(workspace_ref, pane_index)
        surface = self._pane_surface(pane, workspace_ref) if pane else None
        if surface:
            self._api.rename_tab(surface, title)

    # === private: reads and compound steps over the API ===

    def _find_workspace(self) -> str | None:
        workspace = self.workspace()
        return workspace.ref if workspace else None

    def _pane_at_index(self, workspace_ref: str | None, index: int) -> str | None:
        """Pane at ``index`` (left→right; negatives from the right), or None."""
        panes = self._api.list_panes(workspace_ref)
        if -len(panes) <= index < len(panes):
            return panes[index].ref
        return None

    def _pane_surface(
        self, pane_ref: str, workspace_ref: str | None = None
    ) -> str | None:
        """The selected surface of a pane, or None.

        A ``--surface`` handle for a pane without focusing it: focusing a pane
        in another workspace would switch the selected workspace.
        """
        return next(
            (
                p.selected_surface
                for p in self._api.list_panes(workspace_ref)
                if p.ref == pane_ref
            ),
            None,
        )

    def _add_terminal_tab(
        self, workspace_ref: str, pane_ref: str, title: str | None
    ) -> str | None:
        """New terminal tab in ``pane_ref``, renamed to ``title`` if given."""
        surface_ref = self._api.new_surface("terminal", pane_ref, workspace_ref)
        if surface_ref is not None and title:
            self._api.rename_tab(surface_ref, title)
        return surface_ref

    def _split_pane_off(
        self, surface_ref: str, workspace_ref: str | None = None
    ) -> str | None:
        """Split a new pane to the right of ``surface_ref``'s pane; its ref.

        ``new-split --surface`` targets a surface **without focusing it**,
        unlike ``new-pane``, whose focus call would steal workspace focus.
        """
        if not self._api.new_split(surface_ref, "right", workspace_ref):
            return None
        # new-split gives no pane ref. Every split here is off the rightmost
        # pane, so the new pane is now last in left→right order.
        return self._pane_at_index(workspace_ref, -1)

    def _split_new_pane(self, workspace_ref: str | None) -> str | None:
        """Split a new rightmost terminal pane off the workspace, focus-safely."""
        rightmost = self._pane_at_index(workspace_ref, -1)
        if rightmost is None:
            return None
        rightmost_surface = self._pane_surface(rightmost, workspace_ref)
        if rightmost_surface is None:
            return None
        return self._split_pane_off(rightmost_surface, workspace_ref)

    def _open_browser_in_new_pane(self, url: str) -> str | None:
        """Split a new rightmost pane (focus-safe) and open url as a browser.

        The split's placeholder terminal is closed after the browser opens, so
        the pane always keeps a surface. Returns the browser surface ref.
        """
        new_pane = self._split_new_pane(None)
        if not new_pane:
            return None
        placeholder = self._pane_surface(new_pane)
        ref = self._api.new_surface("browser", new_pane, None, url=url)
        if placeholder and placeholder != ref:
            self._api.close_surface(placeholder)
        return ref

    def _find_browser_by_url(self, url_prefix: str) -> Surface | None:
        """First browser surface whose current URL starts with ``url_prefix``.

        Reads each browser's URL; other browsers (docs, unrelated pages) are
        ignored.
        """
        for surface in self._api.list_surfaces(None):
            if surface.type != "browser":
                continue
            url = self._api.browser_url(surface.ref)
            if url and url.startswith(url_prefix):
                return surface
        return None
