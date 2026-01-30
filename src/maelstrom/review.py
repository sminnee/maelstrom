"""Code review utilities for maelstrom."""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import click

from .worktree import run_git


@dataclass
class SquashResult:
    """Result of squash operation."""

    success: bool
    message: str
    fixup_count: int = 0
    commits_affected: int = 0


def get_merge_base(cwd: Path) -> str:
    """Get the merge-base between HEAD and origin/main.

    Args:
        cwd: Working directory.

    Returns:
        The merge-base commit SHA.

    Raises:
        RuntimeError: If merge-base cannot be determined.
    """
    try:
        result = run_git(
            ["merge-base", "HEAD", "origin/main"],
            cwd=cwd,
            quiet=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to find merge-base: {e}") from e


def find_fixup_commits(cwd: Path) -> list[tuple[str, str]]:
    """Find all fixup! commits in the current branch.

    Args:
        cwd: Working directory.

    Returns:
        List of (sha, subject) tuples for fixup commits.
    """
    try:
        merge_base = get_merge_base(cwd)
    except RuntimeError:
        return []

    try:
        log_result = run_git(
            ["log", f"{merge_base}..HEAD", "--format=%H %s"],
            cwd=cwd,
            quiet=True,
        )
    except subprocess.CalledProcessError:
        return []

    fixups = []
    for line in log_result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split(" ", 1)
        if len(parts) != 2:
            continue
        sha, subject = parts
        if subject.startswith("fixup! "):
            fixups.append((sha, subject))

    return fixups


def squash_fixups(cwd: Path) -> SquashResult:
    """Squash all fixup! commits into their targets using autosquash.

    Uses non-interactive rebase with GIT_SEQUENCE_EDITOR=true to
    automatically apply fixup commits.

    Args:
        cwd: Working directory.

    Returns:
        SquashResult with outcome.
    """
    # Find fixup commits first
    fixups = find_fixup_commits(cwd)
    if not fixups:
        return SquashResult(
            success=True,
            message="No fixup commits found",
            fixup_count=0,
        )

    # Get merge-base for rebase
    try:
        merge_base = get_merge_base(cwd)
    except RuntimeError as e:
        return SquashResult(
            success=False,
            message=str(e),
        )

    # Run non-interactive autosquash rebase
    # GIT_SEQUENCE_EDITOR=true makes it non-interactive
    env = os.environ.copy()
    env["GIT_SEQUENCE_EDITOR"] = "true"

    result = subprocess.run(
        ["git", "rebase", "-i", "--autosquash", merge_base],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )

    if result.returncode != 0:
        # Abort the rebase if it failed
        subprocess.run(
            ["git", "rebase", "--abort"],
            cwd=cwd,
            capture_output=True,
        )
        return SquashResult(
            success=False,
            message=f"Rebase failed (conflicts?): {result.stderr}",
            fixup_count=len(fixups),
        )

    # Count unique target commits
    targets = set()
    for _, subject in fixups:
        # Extract original message from "fixup! <original>"
        original = subject[7:]  # Remove "fixup! " prefix
        targets.add(original)

    return SquashResult(
        success=True,
        message=f"Squashed {len(fixups)} fixup commit(s) into {len(targets)} commit(s)",
        fixup_count=len(fixups),
        commits_affected=len(targets),
    )


# --- Click Commands ---


@click.group("review")
def review() -> None:
    """Code review utilities."""
    pass


@review.command("squash")
def cmd_squash() -> None:
    """Squash all fixup! commits into their targets.

    Uses git's autosquash feature to combine fixup commits with
    their original commits. This is typically run after fixing
    issues found during code review.

    Example workflow:
      1. /review-branch - Find issues
      2. Fix issues, commit with: git commit --fixup=<sha>
      3. mael review squash - Combine fixups with originals
    """
    cwd = Path.cwd()

    # Show what we're about to do
    fixups = find_fixup_commits(cwd)
    if not fixups:
        click.echo("No fixup commits found.")
        return

    click.echo(f"Found {len(fixups)} fixup commit(s):")
    for sha, subject in fixups:
        click.echo(f"  {sha[:7]} {subject}")
    click.echo()

    result = squash_fixups(cwd)

    if result.success:
        click.echo(result.message)
    else:
        raise click.ClickException(result.message)


@review.command("status")
def cmd_status() -> None:
    """Show pending fixup commits.

    Lists all fixup! commits that have not yet been squashed
    into their target commits.
    """
    cwd = Path.cwd()
    fixups = find_fixup_commits(cwd)

    if not fixups:
        click.echo("No pending fixup commits.")
        return

    click.echo(f"Pending fixup commits ({len(fixups)}):")
    for sha, subject in fixups:
        # Extract target from "fixup! <target message>"
        target = subject[7:]
        click.echo(f"  {sha[:7]} -> {target}")
    click.echo()
    click.echo("Run 'mael review squash' to combine with originals.")
