"""Build a reviewable bundle (log + diff) for a commit range.

Used by the `/code-review` skill to package commits for a review sub-agent.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import click

from .worktree import run_git


SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
DEFAULT_RANGE = "origin/main..HEAD"


@dataclass
class DescribeResult:
    """Result of describe-change."""

    range: str
    log: str
    diff: str

    def render(self) -> str:
        return f"Range: {self.range}\n\n## Log\n\n{self.log}\n\n## Diff\n\n{self.diff}\n"


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


def describe_change(cwd: Path, rng: str) -> DescribeResult:
    """Run `git log` and `git diff` for the resolved range."""
    log = run_git(
        ["log", "--reverse", "--pretty=fuller", rng],
        cwd=cwd,
        quiet=True,
    ).stdout
    diff = run_git(["diff", rng], cwd=cwd, quiet=True).stdout
    return DescribeResult(range=rng, log=log, diff=diff)


@click.command("describe-change")
@click.argument("range_arg", metavar="[RANGE]", required=False, default=None)
def cmd_describe_change(range_arg: str | None) -> None:
    """Print a reviewable bundle (log + diff) for a commit range.

    \b
    RANGE defaults to `origin/main..HEAD`. A bare SHA is expanded to
    `<sha>^..<sha>`. Any other argument is passed to git as-is.

    \b
    Aborts (exit 1) if:
      - the worktree has uncommitted changes
      - the resolved range contains no commits
      - the range is not a valid git revision range

    Used by the `/code-review` skill to build sub-agent context.
    """
    cwd = Path.cwd()
    check_clean_worktree(cwd)
    rng = resolve_range(range_arg)
    check_range_non_empty(cwd, rng)
    result = describe_change(cwd, rng)
    click.echo(result.render(), nl=False)
