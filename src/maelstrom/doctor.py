"""Project health checks and auto-fixes for maelstrom projects."""

import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .ports import load_port_allocations, remove_port_allocation
from .worktree import (
    ENV_SECTION_END,
    ENV_SECTION_START,
    MAIN_BRANCH,
    extract_worktree_name_from_folder,
    list_worktrees,
    run_cmd,
    run_git,
    update_local_main,
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


@dataclass
class DoctorResult:
    project_name: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def issues_found(self) -> int:
        return sum(1 for c in self.checks if c.status in (CheckStatus.FIXED, CheckStatus.WARNING, CheckStatus.ERROR))

    @property
    def fixed_count(self) -> int:
        return sum(1 for c in self.checks if c.status == CheckStatus.FIXED)

    @property
    def attention_count(self) -> int:
        return sum(1 for c in self.checks if c.status in (CheckStatus.WARNING, CheckStatus.ERROR))


def _check_mael_marker(project_path: Path) -> CheckResult:
    """Check that the .mael marker file exists."""
    if (project_path / ".mael").exists():
        return CheckResult(CheckStatus.OK, ".mael marker exists")
    return CheckResult(CheckStatus.ERROR, ".mael marker missing — not a maelstrom project")


def _check_core_bare(project_path: Path) -> CheckResult:
    """Check that core.bare = false."""
    result = run_cmd(
        ["git", "config", "--get", "core.bare"],
        cwd=project_path,
        quiet=True,
        check=False,
    )
    value = result.stdout.strip() if result.returncode == 0 else ""
    if value == "false":
        return CheckResult(CheckStatus.OK, "core.bare = false")

    # Auto-fix
    try:
        run_git(["config", "core.bare", "false"], cwd=project_path)
        return CheckResult(CheckStatus.FIXED, f"core.bare was '{value or 'unset'}' → fixed to false")
    except subprocess.CalledProcessError:
        return CheckResult(CheckStatus.ERROR, f"core.bare is '{value or 'unset'}' and could not be fixed")


def _check_standard_fetch_refspec(project_path: Path) -> CheckResult:
    """Check that the standard fetch refspec is configured."""
    expected = "+refs/heads/*:refs/remotes/origin/*"
    result = run_cmd(
        ["git", "config", "--get-all", "remote.origin.fetch"],
        cwd=project_path,
        quiet=True,
        check=False,
    )
    refspecs = result.stdout.splitlines() if result.returncode == 0 else []

    if expected in refspecs:
        return CheckResult(CheckStatus.OK, "Standard fetch refspec configured")

    # Auto-fix
    try:
        if not refspecs:
            run_git(["config", "remote.origin.fetch", expected], cwd=project_path)
        else:
            run_git(["config", "--add", "remote.origin.fetch", expected], cwd=project_path)
        return CheckResult(CheckStatus.FIXED, "Standard fetch refspec was missing → added")
    except subprocess.CalledProcessError:
        return CheckResult(CheckStatus.ERROR, "Standard fetch refspec missing and could not be added")


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
    """Check that origin/main exists."""
    result = run_cmd(
        ["git", "rev-parse", "--verify", f"origin/{MAIN_BRANCH}"],
        cwd=project_path,
        quiet=True,
        check=False,
    )
    if result.returncode == 0:
        return CheckResult(CheckStatus.OK, f"origin/{MAIN_BRANCH} exists")
    return CheckResult(CheckStatus.ERROR, f"origin/{MAIN_BRANCH} does not exist — try 'git fetch origin'")


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
            wt_path = Path(line[len("worktree "):])
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
        name for name in project_allocs
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
        return CheckResult(CheckStatus.OK, ".env section markers valid in all worktrees")

    return CheckResult(
        CheckStatus.WARNING,
        f".env marker issues: {'; '.join(issues)}",
    )


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
        _check_origin_remote,
        _check_origin_main,
        _check_local_main_sync,
        _check_stale_worktrees,
        _check_port_allocations,
        _check_env_markers,
    ]

    for check in checks:
        check_result = check(project_path)
        result.checks.append(check_result)

        # Stop early if .mael marker is missing — not a maelstrom project
        if check is _check_mael_marker and check_result.status == CheckStatus.ERROR:
            break

    return result
