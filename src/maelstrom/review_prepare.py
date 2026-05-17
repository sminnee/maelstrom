"""Pre-flight gates and command emission for the `/code-review` skill.

Resolves a commit range, verifies the worktree is clean and the range is
non-empty, and prints the git commands a review sub-agent should run. The
sub-agent runs the commands itself, so the (potentially large) log/diff
output never passes through the parent agent's context.
"""

import re
import subprocess
from pathlib import Path

import click

from .worktree import run_git


SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
DEFAULT_RANGE = "origin/main..HEAD"


def resolve_range(arg: str | None) -> str:
    """Resolve the user's range argument to a git range expression.

    - Empty/None: default `origin/main..HEAD`.
    - Single SHA (7-40 hex chars): `<sha>^..<sha>`.
    - Otherwise: passed through verbatim.
    """
    if not arg:
        return DEFAULT_RANGE
    if SHA_RE.match(arg):
        return f"{arg}^..{arg}"
    return arg


def check_clean_worktree(cwd: Path) -> None:
    """Fail if the worktree has uncommitted changes."""
    result = run_git(["status", "--porcelain"], cwd=cwd, quiet=True)
    if result.stdout.strip():
        raise click.ClickException("Commit your work before reviewing.")


def check_range_non_empty(cwd: Path, rng: str) -> None:
    """Fail if the range contains no commits."""
    try:
        result = run_git(["rev-list", "--count", rng], cwd=cwd, quiet=True)
    except subprocess.CalledProcessError as e:
        raise click.ClickException(f"Invalid range '{rng}': {e.stderr.strip() or e}")
    if result.stdout.strip() == "0":
        raise click.ClickException("No commits to review.")


def render(rng: str) -> str:
    """Return the ready-to-run git commands for the sub-agent."""
    return (
        f"Range: {rng}\n"
        "\n"
        "Run these to inspect the change:\n"
        f"  git log --reverse --pretty=fuller {rng}\n"
        f"  git diff {rng}\n"
    )


@click.command("review-prepare")
@click.argument("range_arg", metavar="[RANGE]", required=False, default=None)
def cmd_review_prepare(range_arg: str | None) -> None:
    """Print the git commands a review sub-agent should run for a commit range.

    \b
    RANGE defaults to `origin/main..HEAD`. A bare SHA is expanded to
    `<sha>^..<sha>`. Any other argument is passed to git as-is.

    \b
    Aborts (exit 1) if:
      - the worktree has uncommitted changes
      - the resolved range contains no commits
      - the range is not a valid git revision range

    Used by the `/code-review` skill to gate review and hand the sub-agent
    the git commands to run itself.
    """
    cwd = Path.cwd()
    check_clean_worktree(cwd)
    rng = resolve_range(range_arg)
    check_range_non_empty(cwd, rng)
    click.echo(render(rng), nl=False)
