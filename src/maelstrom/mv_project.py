"""Pure model for renaming a maelstrom project.

A project's *name* is load-bearing: it is ``project_path.name``, and the worktree
folder names, task directories, env/log directories, Claude Code state dirs and
every task's derived session id all follow from it. This module turns a set of
facts about a project into a :class:`MovePlan` describing every rename that a
safe rename must perform.

It is the model layer (see ``docs/dev/architecture-patterns.md``): no
``subprocess``, no filesystem, no printing. Facts go in as arguments, a plan
comes out, and invalid input raises ``ValueError``. The IO adapter
``mv_project_cli.py`` gathers the facts and applies the plan.
"""

from dataclasses import dataclass, field
from pathlib import Path

from .worktree_model import WORKTREE_NAMES, sanitise_path_for_claude


@dataclass(frozen=True)
class DirMove:
    """One directory rename.

    ``is_worktree`` marks the moves that git must be told about afterwards, and
    ``nato`` carries the worktree's NATO name when it has one. A worktree whose
    folder does not follow ``<project>-<nato>`` (notably ``_main``) keeps its
    name, so ``src == dst`` and ``nato`` is ``None``.
    """

    src: Path
    dst: Path
    is_worktree: bool
    nato: str | None = None

    @property
    def renamed(self) -> bool:
        """Whether this move actually changes the path."""
        return self.src != self.dst


@dataclass(frozen=True)
class MovePlan:
    """Everything that must change to rename a project, computed up front.

    The plan is inert data: building it touches nothing. ``--dry-run`` renders it
    and stops; a real run hands it to the adapter's apply helpers.
    """

    old_name: str
    new_name: str
    old_project_path: Path
    new_project_path: Path
    dir_moves: list[DirMove]
    port_key_old: str
    port_key_new: str
    claude_dir_moves: list[tuple[Path, Path]]
    claude_json_rekeys: list[tuple[str, str]]
    symlink_repoints: list[tuple[Path, Path]]
    task_rekeys: list[tuple[str, str]]
    orphaned_session_count: int
    warnings: list[str] = field(default_factory=list)

    @property
    def worktree_moves(self) -> list[DirMove]:
        """The worktree directory moves, in plan order."""
        return [m for m in self.dir_moves if m.is_worktree]


def new_worktree_folder(old_project: str, new_project: str, folder: str) -> str:
    """Map a worktree folder name from the old project name to the new one.

    ``<old>-<nato>`` becomes ``<new>-<nato>``. Anything else is returned
    unchanged — most importantly ``_main``, which is a real worktree that does
    not follow the naming convention, and lookalikes such as ``old-alphabet``
    whose suffix is not a NATO name.
    """
    nato = worktree_nato_name(old_project, folder)
    if nato is None:
        return folder
    return f"{new_project}-{nato}"


def worktree_nato_name(project: str, folder: str) -> str | None:
    """Return the NATO name in ``folder``, or ``None`` if it has none.

    Unlike :func:`maelstrom.worktree_model.extract_worktree_name_from_folder`
    this lives here so the rename model stays independent of how folders are
    discovered; the two agree on what counts as a worktree folder.
    """
    prefix = f"{project}-"
    if not folder.startswith(prefix):
        return None
    suffix = folder[len(prefix) :]
    return suffix if suffix in WORKTREE_NAMES else None


def rekey_port_allocations(
    allocations: dict[str, dict[str, int]], old_key: str, new_key: str
) -> dict[str, dict[str, int]]:
    """Return a copy of ``allocations`` with the project entry moved.

    Port allocations are keyed by absolute project path, so a rename must move
    the whole entry. Unrelated projects are preserved untouched and the input is
    never mutated.

    Raises:
        ValueError: If ``new_key`` already holds allocations — overwriting it
            would silently discard another project's port bases.
    """
    if new_key in allocations and old_key != new_key:
        raise ValueError(
            f"Port allocations already exist for {new_key}; refusing to overwrite"
        )
    result = dict(allocations)
    entry = result.pop(old_key, None)
    if entry is not None:
        result[new_key] = entry
    return result


def rekey_claude_json(data: dict, rekeys: list[tuple[str, str]]) -> dict:
    """Return a copy of ``~/.claude.json`` data with project paths re-keyed.

    Two places name project paths: the top-level ``projects`` dict (trust and
    per-project permissions) and any ``githubRepoPaths`` list. Both are re-keyed
    from ``rekeys``; keys not mentioned are left alone. An existing entry under a
    new key wins, so a rename never clobbers real settings for the target path.
    """
    result = dict(data)
    mapping = dict(rekeys)

    projects = result.get("projects")
    if isinstance(projects, dict):
        moved: dict = {}
        for key, value in projects.items():
            moved.setdefault(mapping.get(key, key), value)
        result["projects"] = moved

    repo_paths = result.get("githubRepoPaths")
    if isinstance(repo_paths, list):
        result["githubRepoPaths"] = [
            mapping.get(p, p) if isinstance(p, str) else p for p in repo_paths
        ]

    return result


def repoint_path(
    path: Path,
    old_root: Path,
    new_root: Path,
    *,
    folder_map: dict[str, str] | None = None,
) -> Path | None:
    """Rewrite ``path`` from under ``old_root`` to under ``new_root``.

    The project directory is not the only component that moves: the worktree
    folder directly beneath it is renamed too. ``folder_map`` supplies that
    old-folder -> new-folder mapping, so a path under ``old/old-alpha`` lands at
    ``new/new-alpha`` and not at the non-existent ``new/old-alpha``.

    Returns ``None`` when ``path`` is not under ``old_root``, so callers can use
    it as both the test and the transform.
    """
    try:
        relative = path.relative_to(old_root)
    except ValueError:
        return None
    parts = relative.parts
    if folder_map and parts:
        renamed = folder_map.get(parts[0])
        if renamed is not None:
            relative = Path(renamed).joinpath(*parts[1:])
    return new_root / relative


def build_move_plan(
    *,
    old_name: str,
    new_name: str,
    projects_dir: Path,
    worktree_folders: list[str],
    task_ids: list[str],
    ran_task_ids: set[str],
    home: Path,
    claude_json_projects: list[str] | None = None,
    global_symlinks: list[tuple[Path, Path]] | None = None,
    task_statuses: dict[str, str] | None = None,
) -> MovePlan:
    """Compute every rename needed to move project ``old_name`` to ``new_name``.

    Args:
        old_name: Current project name.
        new_name: Requested new project name.
        projects_dir: The directory both names live under.
        worktree_folders: Folder names of the project's real worktrees, as
            reported by git. Includes non-conventional ones such as ``_main``.
        task_ids: Every task id under the project.
        ran_task_ids: The subset of ``task_ids`` that already has a Claude
            transcript — these are the sessions the rename orphans.
        home: The user's home directory, so Claude paths are testable.
        claude_json_projects: Path keys present in ``~/.claude.json``. Only keys
            under the old project path are re-keyed.
        global_symlinks: ``(link, target)`` pairs for ``~/.claude/skills`` and
            ``~/.claude/commands``. Only those pointing into the old project
            path are re-pointed.
        task_statuses: Task id -> status folder, used to build store keys.

    Returns:
        The :class:`MovePlan`. Building it changes nothing on disk.

    Raises:
        ValueError: If either name is empty, they are equal, or a name is not a
            single path component.
    """
    if not old_name:
        raise ValueError("Project name cannot be empty")
    if not new_name:
        raise ValueError("Project name cannot be empty")
    if old_name == new_name:
        raise ValueError(f"Old and new project names are the same: '{old_name}'")
    for name in (old_name, new_name):
        if "/" in name or name in (".", ".."):
            raise ValueError(f"Invalid project name '{name}'")

    old_project_path = projects_dir / old_name
    new_project_path = projects_dir / new_name

    # Worktree folders move *inside* the old project dir first; the project dir
    # itself moves last, so every intermediate path stays valid and a failure
    # part-way leaves the project findable under its old name.
    dir_moves: list[DirMove] = []
    for folder in sorted(worktree_folders):
        nato = worktree_nato_name(old_name, folder)
        dir_moves.append(
            DirMove(
                src=old_project_path / folder,
                dst=old_project_path / new_worktree_folder(old_name, new_name, folder),
                is_worktree=True,
                nato=nato,
            )
        )
    dir_moves.append(
        DirMove(src=old_project_path, dst=new_project_path, is_worktree=False)
    )

    # Claude keys off the *final* absolute path of each directory, so its slugs
    # are computed from where things land, not from the intermediate location.
    # The slug is lossy, so it is always derived forwards from a path.
    final_paths: list[tuple[Path, Path]] = [(old_project_path, new_project_path)]
    for move in dir_moves:
        if not move.is_worktree:
            continue
        final_paths.append((move.src, new_project_path / move.dst.name))

    claude_projects_dir = home / ".claude" / "projects"
    claude_dir_moves = [
        (
            claude_projects_dir / sanitise_path_for_claude(old_path),
            claude_projects_dir / sanitise_path_for_claude(new_path),
        )
        for old_path, new_path in final_paths
    ]

    # Paths under the project also cross a renamed worktree folder, so every
    # rewrite below maps that component too.
    folder_map = {
        move.src.name: move.dst.name for move in dir_moves if move.is_worktree
    }

    claude_json_rekeys: list[tuple[str, str]] = []
    for key in claude_json_projects or []:
        repointed = repoint_path(
            Path(key), old_project_path, new_project_path, folder_map=folder_map
        )
        if repointed is not None and str(repointed) != key:
            claude_json_rekeys.append((key, str(repointed)))

    symlink_repoints: list[tuple[Path, Path]] = []
    for link, target in global_symlinks or []:
        repointed = repoint_path(
            target, old_project_path, new_project_path, folder_map=folder_map
        )
        if repointed is not None and repointed != target:
            symlink_repoints.append((link, repointed))

    statuses = task_statuses or {}
    task_rekeys = [
        (
            f"{old_name}/{statuses.get(task_id, 'todo')}/{task_id}.md",
            f"{new_name}/{statuses.get(task_id, 'todo')}/{task_id}.md",
        )
        for task_id in sorted(task_ids)
    ]

    orphaned = len({t for t in ran_task_ids if t in set(task_ids)})

    warnings: list[str] = []
    if orphaned:
        warnings.append(
            f"{orphaned} task(s) have existing Claude sessions. Session ids "
            f"derive from the project name, so those sessions are orphaned — "
            f"`mael task run` will start fresh rather than resume."
        )

    return MovePlan(
        old_name=old_name,
        new_name=new_name,
        old_project_path=old_project_path,
        new_project_path=new_project_path,
        dir_moves=dir_moves,
        port_key_old=str(old_project_path),
        port_key_new=str(new_project_path),
        claude_dir_moves=claude_dir_moves,
        claude_json_rekeys=claude_json_rekeys,
        symlink_repoints=symlink_repoints,
        task_rekeys=task_rekeys,
        orphaned_session_count=orphaned,
        warnings=warnings,
    )
