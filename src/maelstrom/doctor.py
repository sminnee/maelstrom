"""Project health checks and auto-fixes for maelstrom projects."""

import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .context import (
    GLOBAL_CONFIG_FILENAME,
    GLOBAL_CONFIG_FILENAME_LEGACY,
    get_maelstrom_dir,
)
from .ports import ALLOCATIONS_FILENAME, load_port_allocations, remove_port_allocation
from .shell import run_cmd
from .util import harden_path
from .worktree import (
    list_worktrees,
    run_git,
    update_local_main,
)
from .worktree_model import (
    ENV_SECTION_END,
    ENV_SECTION_START,
    MAIN_BRANCH,
    MAIN_WORKTREE_FOLDER,
    extract_worktree_name_from_folder,
)


class CheckStatus(Enum):
    OK = "ok"
    FIXED = "fixed"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class CheckResult:
    status: CheckStatus
    message: str
    #: Stable identifier for the check that produced this result, e.g.
    #: ``main_upstream``. ``run_doctor`` fills it from the check function's own
    #: name, so callers can select a result without matching on ``message``,
    #: which is prose and free to change. Empty on a hand-built result.
    name: str = ""


@dataclass
class DoctorResult:
    project_name: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def issues_found(self) -> int:
        return sum(
            1
            for c in self.checks
            if c.status in (CheckStatus.FIXED, CheckStatus.WARNING, CheckStatus.ERROR)
        )

    @property
    def fixed_count(self) -> int:
        return sum(1 for c in self.checks if c.status == CheckStatus.FIXED)

    @property
    def attention_count(self) -> int:
        return sum(
            1
            for c in self.checks
            if c.status in (CheckStatus.WARNING, CheckStatus.ERROR)
        )


def _git_config(project_path: Path, key: str) -> str:
    """Read one git config value, or "" when the key is unset."""
    result = run_cmd(
        ["git", "config", "--get", key],
        cwd=project_path,
        quiet=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_config_all(project_path: Path, key: str) -> list[str]:
    """Read every value of a multi-valued git config key."""
    result = run_cmd(
        ["git", "config", "--get-all", key],
        cwd=project_path,
        quiet=True,
        check=False,
    )
    return result.stdout.splitlines() if result.returncode == 0 else []


def _default_branch(project_path: Path) -> str:
    """The project's default branch, e.g. ``main``, ``develop`` or ``master``.

    Reads ``refs/remotes/origin/HEAD``, which the clone writes and ``git remote
    set-head`` repairs. Falls back to :data:`MAIN_BRANCH` when that symref is
    missing, which is the case for a project cloned before maelstrom fetched.

    Most of maelstrom still assumes ``main`` — see ``MAIN_BRANCH`` in
    ``worktree_model``. This function is deliberately local to doctor, whose
    repairs would otherwise report a project on ``develop`` as broken.
    """
    result = run_cmd(
        ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        cwd=project_path,
        quiet=True,
        check=False,
    )
    if result.returncode != 0:
        return MAIN_BRANCH
    ref = result.stdout.strip()
    return ref.removeprefix("origin/") or MAIN_BRANCH


def _check_mael_marker(project_path: Path) -> CheckResult:
    """Check that the .mael marker file exists."""
    if (project_path / ".mael").exists():
        return CheckResult(CheckStatus.OK, ".mael marker exists")
    return CheckResult(
        CheckStatus.ERROR, ".mael marker missing — not a maelstrom project"
    )


def _check_core_bare(project_path: Path) -> CheckResult:
    """Check that core.bare = true (project root should be bare, not a working tree)."""
    value = _git_config(project_path, "core.bare")
    if value == "true":
        return CheckResult(CheckStatus.OK, "core.bare = true")

    # Auto-fix
    try:
        run_git(["config", "core.bare", "true"], cwd=project_path)
        return CheckResult(
            CheckStatus.FIXED, f"core.bare was '{value or 'unset'}' → fixed to true"
        )
    except subprocess.CalledProcessError:
        return CheckResult(
            CheckStatus.ERROR,
            f"core.bare is '{value or 'unset'}' and could not be fixed",
        )


def _check_standard_fetch_refspec(project_path: Path) -> CheckResult:
    """Check that the standard fetch refspec is configured."""
    expected = "+refs/heads/*:refs/remotes/origin/*"
    refspecs = _git_config_all(project_path, "remote.origin.fetch")

    if expected in refspecs:
        return CheckResult(CheckStatus.OK, "Standard fetch refspec configured")

    # Auto-fix
    try:
        if not refspecs:
            run_git(["config", "remote.origin.fetch", expected], cwd=project_path)
        else:
            run_git(
                ["config", "--add", "remote.origin.fetch", expected], cwd=project_path
            )
        return CheckResult(
            CheckStatus.FIXED, "Standard fetch refspec was missing → added"
        )
    except subprocess.CalledProcessError:
        return CheckResult(
            CheckStatus.ERROR, "Standard fetch refspec missing and could not be added"
        )


def _check_notes_rewrite_ref(project_path: Path) -> CheckResult:
    """Check that git notes survive a rebase.

    ``/code-review`` tags a reviewed commit with a git note so a later run skips
    it. Without ``notes.rewriteRef`` every rebase drops the note, so nothing is
    ever skipped and the feature silently does nothing.
    """
    expected = "refs/notes/*"
    values = _git_config_all(project_path, "notes.rewriteRef")

    if expected in values:
        return CheckResult(CheckStatus.OK, "notes.rewriteRef configured")

    # Auto-fix
    try:
        if not values:
            run_git(["config", "notes.rewriteRef", expected], cwd=project_path)
        else:
            run_git(["config", "--add", "notes.rewriteRef", expected], cwd=project_path)
        return CheckResult(CheckStatus.FIXED, "notes.rewriteRef was missing → added")
    except subprocess.CalledProcessError:
        return CheckResult(
            CheckStatus.ERROR, "notes.rewriteRef missing and could not be added"
        )


def _check_local_main_sync(project_path: Path) -> CheckResult:
    """Try to fast-forward local main to match origin/main."""
    result = update_local_main(project_path)
    if result.status == "updated":
        return CheckResult(CheckStatus.FIXED, result.message)
    elif result.status == "warning":
        return CheckResult(CheckStatus.WARNING, result.message)
    return CheckResult(CheckStatus.OK, result.message)


def _check_origin_remote(project_path: Path) -> CheckResult:
    """Check that the origin remote exists."""
    result = run_cmd(
        ["git", "remote", "get-url", "origin"],
        cwd=project_path,
        quiet=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return CheckResult(CheckStatus.OK, "origin remote configured")
    return CheckResult(CheckStatus.ERROR, "origin remote not configured")


def _check_origin_main(project_path: Path) -> CheckResult:
    """Check that the remote default branch exists locally."""
    branch = _default_branch(project_path)
    result = run_cmd(
        ["git", "rev-parse", "--verify", f"origin/{branch}"],
        cwd=project_path,
        quiet=True,
        check=False,
    )
    if result.returncode == 0:
        return CheckResult(CheckStatus.OK, f"origin/{branch} exists")
    return CheckResult(
        CheckStatus.ERROR, f"origin/{branch} does not exist — try 'git fetch origin'"
    )


def _check_main_upstream(project_path: Path) -> CheckResult:
    """Check that the default branch tracks its remote branch.

    A bare clone writes no ``branch.<name>.remote`` or ``branch.<name>.merge``,
    so the branch tracks nothing. Maelstrom always names the remote branch
    explicitly, so the gap only bites a human working in ``_main``: no
    ahead/behind count, and a bare ``git pull`` or ``git push`` fails. The
    config is repo-scoped, so it reads the same from the project root as from
    ``_main``.
    """
    branch = _default_branch(project_path)
    remote = _git_config(project_path, f"branch.{branch}.remote")
    merge = _git_config(project_path, f"branch.{branch}.merge")
    if remote == "origin" and merge == f"refs/heads/{branch}":
        return CheckResult(CheckStatus.OK, f"{branch} upstream is origin/{branch}")

    # An upstream pointing elsewhere is repointed, not left alone: maelstrom
    # owns the layout and rebases against the remote default branch throughout.
    # Name what was there, so a deliberate upstream is not overwritten silently.
    was = (
        f"tracked {remote}/{merge.rsplit('/', 1)[-1]}"
        if remote and merge
        else "had no upstream"
    )

    # Auto-fix. _check_origin_main runs first and reports a missing remote
    # branch, so this check does not repeat that diagnosis.
    try:
        run_git(
            ["branch", f"--set-upstream-to=origin/{branch}", branch],
            cwd=project_path,
        )
        return CheckResult(
            CheckStatus.FIXED,
            f"{branch} {was} → set to origin/{branch}",
        )
    except subprocess.CalledProcessError:
        return CheckResult(
            CheckStatus.ERROR,
            f"{branch} {was} and could not be set to origin/{branch}",
        )


def _check_main_worktree(project_path: Path) -> CheckResult:
    """Check that the default branch is checked out in ``_main``.

    Projects created before ``_main`` existed hold the branch in a NATO
    worktree, which burns a workspace. Reported rather than fixed: moving the
    branch means moving a checkout the user may be sitting in.
    """
    branch = _default_branch(project_path)
    # git reports resolved paths, so resolve before comparing.
    main_path = (project_path / MAIN_WORKTREE_FOLDER).resolve()
    add_cmd = f"git -C {project_path} worktree add {MAIN_WORKTREE_FOLDER} {branch}"

    for wt in list_worktrees(project_path):
        if wt.branch != branch:
            continue
        if wt.path.resolve() == main_path:
            return CheckResult(
                CheckStatus.OK, f"{branch} is checked out in {MAIN_WORKTREE_FOLDER}"
            )
        # git allows one worktree per branch, so the branch must be freed before
        # it can be added at _main. A bare `worktree add` here would fail.
        return CheckResult(
            CheckStatus.WARNING,
            f"{branch} is checked out in {wt.path.name}, not "
            f"{MAIN_WORKTREE_FOLDER} — that worktree cannot be used for work. "
            f"Move it with: git -C {wt.path} checkout --detach && {add_cmd}",
        )

    # No worktree holds the branch. A leftover _main directory is the awkward
    # case: it is not a checkout of the branch, and if it is a detached worktree
    # it can be recycled as a feature workspace.
    if main_path.exists():
        return CheckResult(
            CheckStatus.WARNING,
            f"{MAIN_WORKTREE_FOLDER} exists but does not hold {branch} — "
            f"remove it and recreate with: git -C {project_path} worktree remove "
            f"{MAIN_WORKTREE_FOLDER} && {add_cmd}",
        )
    return CheckResult(
        CheckStatus.WARNING,
        f"No {MAIN_WORKTREE_FOLDER} worktree — create it with: {add_cmd}",
    )


def _check_stale_worktrees(project_path: Path) -> CheckResult:
    """Check for stale worktree entries and prune them."""
    # Check if there are any stale entries
    result = run_cmd(
        ["git", "worktree", "list", "--porcelain"],
        cwd=project_path,
        quiet=True,
        check=False,
    )
    if result.returncode != 0:
        return CheckResult(CheckStatus.OK, "Could not list worktrees")

    # Look for worktree paths that don't exist on disk
    stale_paths = []
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            wt_path = Path(line[len("worktree ") :])
            if not wt_path.exists() and wt_path != project_path:
                stale_paths.append(wt_path)

    if not stale_paths:
        return CheckResult(CheckStatus.OK, "No stale worktree entries")

    # Auto-fix with git worktree prune
    try:
        run_git(["worktree", "prune"], cwd=project_path)
        return CheckResult(
            CheckStatus.FIXED,
            f"Pruned {len(stale_paths)} stale worktree entry(ies)",
        )
    except subprocess.CalledProcessError:
        return CheckResult(
            CheckStatus.WARNING,
            f"Found {len(stale_paths)} stale worktree entry(ies) but could not prune",
        )


def _check_port_allocations(project_path: Path) -> CheckResult:
    """Check that port allocations match actual worktrees."""
    project_key = str(project_path.resolve())
    allocations = load_port_allocations()
    project_allocs = allocations.get(project_key, {})

    if not project_allocs:
        return CheckResult(CheckStatus.OK, "No port allocations to check")

    # Get actual worktree names
    worktrees = list_worktrees(project_path)
    project_name = project_path.name
    actual_names: set[str] = set()
    for wt in worktrees:
        if wt.path == project_path:
            continue
        name = extract_worktree_name_from_folder(project_name, wt.path.name)
        if name:
            actual_names.add(name)

    # Find orphaned allocations (allocated but no worktree, excluding _shared)
    orphans = [
        name
        for name in project_allocs
        if name != "_shared" and name not in actual_names
    ]

    if not orphans:
        return CheckResult(CheckStatus.OK, "Port allocations consistent with worktrees")

    # Auto-fix: remove orphaned allocations
    for name in orphans:
        remove_port_allocation(project_path, name)

    return CheckResult(
        CheckStatus.FIXED,
        f"Removed {len(orphans)} orphaned port allocation(s): {', '.join(orphans)}",
    )


def _check_env_markers(project_path: Path) -> CheckResult:
    """Check that .env files in worktrees have valid maelstrom section markers."""
    worktrees = list_worktrees(project_path)
    issues = []

    for wt in worktrees:
        if wt.path == project_path:
            continue
        env_file = wt.path / ".env"
        if not env_file.exists():
            continue

        content = env_file.read_text()
        has_start = ENV_SECTION_START in content
        has_end = ENV_SECTION_END in content

        name = wt.path.name
        if has_start and not has_end:
            issues.append(f"{name}: missing end marker")
        elif has_end and not has_start:
            issues.append(f"{name}: missing start marker")

    if not issues:
        return CheckResult(
            CheckStatus.OK, ".env section markers valid in all worktrees"
        )

    return CheckResult(
        CheckStatus.WARNING,
        f".env marker issues: {'; '.join(issues)}",
    )


def _check_secret_file_perms(project_path: Path) -> CheckResult:
    """Check (and auto-fix) that secret-bearing files are not group/other readable.

    Tightens ``~/.maelstrom/`` to 0o700; ``config.yaml`` (and legacy
    ``~/.maelstrom.yaml``) and ``port_allocations.json`` to 0o600; and every
    worktree ``.env`` to 0o600. Only narrows perms (via :func:`util.harden_path`),
    never widens. Returns OK when nothing was loose, FIXED listing what was
    tightened, or WARNING naming any path that could not be fixed.
    """
    maelstrom_dir = get_maelstrom_dir()

    # (path, target_mode, label) — label is what we report when tightened.
    targets: list[tuple[Path, int, str]] = [
        (maelstrom_dir, 0o700, "~/.maelstrom"),
        (maelstrom_dir / GLOBAL_CONFIG_FILENAME, 0o600, "config.yaml"),
        (Path.home() / GLOBAL_CONFIG_FILENAME_LEGACY, 0o600, "~/.maelstrom.yaml"),
        (maelstrom_dir / ALLOCATIONS_FILENAME, 0o600, ALLOCATIONS_FILENAME),
    ]

    # Every worktree .env — enumerated exactly as _check_env_markers does.
    for wt in list_worktrees(project_path):
        if wt.path == project_path:
            continue
        targets.append((wt.path / ".env", 0o600, f"{wt.path.name}/.env"))

    tightened: list[str] = []
    unfixable: list[str] = []
    for path, mode, label in targets:
        if not path.exists():
            continue
        try:
            if harden_path(path, mode):
                tightened.append(label)
        except OSError:
            unfixable.append(label)

    if unfixable:
        return CheckResult(
            CheckStatus.WARNING,
            f"could not tighten perms on: {', '.join(unfixable)}",
        )
    if tightened:
        return CheckResult(
            CheckStatus.FIXED,
            f"tightened perms on: {', '.join(tightened)}",
        )
    return CheckResult(CheckStatus.OK, "secret file permissions are restrictive")


def run_doctor(project_path: Path) -> DoctorResult:
    """Run all health checks on a project.

    Args:
        project_path: Path to the project root.

    Returns:
        DoctorResult with all check results.
    """
    project_name = project_path.name
    result = DoctorResult(project_name=project_name)

    checks = [
        _check_mael_marker,
        _check_core_bare,
        _check_standard_fetch_refspec,
        _check_notes_rewrite_ref,
        _check_origin_remote,
        _check_origin_main,
        _check_main_upstream,
        _check_local_main_sync,
        _check_main_worktree,
        _check_stale_worktrees,
        _check_port_allocations,
        _check_env_markers,
        _check_secret_file_perms,
    ]

    for check in checks:
        check_result = check(project_path)
        # Name the result after the check that produced it, so no check has to
        # repeat its own name and the two can never disagree.
        check_result.name = check.__name__.removeprefix("_check_")
        result.checks.append(check_result)

        # Stop early if .mael marker is missing — not a maelstrom project
        if check is _check_mael_marker and check_result.status == CheckStatus.ERROR:
            break

    return result
