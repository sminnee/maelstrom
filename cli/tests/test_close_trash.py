"""Tests for `mael close --trash`: which sequence a close reaches, and how it ends.

The sequence itself is specified in ``lib/domain/tests/test_worktree_trash.py``.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from mael_cli.cli import cli
from mael_domain.context import ResolvedContext
from mael_domain.task import list_tasks
from mael_domain.task_table import InMemoryTaskTable
from mael_domain.worktree import CloseResult
from mael_domain.worktree_close import FullCloseResult

TRASHED = FullCloseResult(
    close=CloseResult(
        success=True,
        message="Trashed feature/x as trash/feature/x.",
        branch="feature/x",
        # What a forced close would write a reopen task for.
        had_unmerged_work=True,
    ),
    messages=["Closed PR #7.", "Moved feature/x to trash/feature/x."],
)
REFUSED = FullCloseResult(
    close=CloseResult(success=False, message="trash/old is already in the trash")
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project folder holding the worktrees ``alpha`` and ``bravo``."""
    root = tmp_path / "myproject"
    for nato in ("alpha", "bravo"):
        (root / f"myproject-{nato}").mkdir(parents=True)
    return root


def _invoke(project: Path, args, *, results=(TRASHED,)):
    def resolve(target, **_kwargs):
        return ResolvedContext(
            projects_dir=project.parent,
            project="myproject",
            worktree=target.partition(".")[2],
        )

    table = InMemoryTaskTable()
    with (
        patch("mael_cli.cli.resolve_context", side_effect=resolve),
        patch(
            "mael_cli.cli.trash_worktree_fully", new=AsyncMock(side_effect=results)
        ) as trash,
        patch("mael_cli.cli.close_worktree_fully", new=AsyncMock()) as close,
        patch("mael_cli.cli.task_table", new=AsyncMock(return_value=table)),
    ):
        outcome = CliRunner().invoke(cli, ["close", *args])
    return outcome, trash, close, asyncio.run(list_tasks(table, project="myproject"))


def test_trash_runs_the_trash_sequence_in_place_of_the_close(project):
    outcome, trash, close, tasks = _invoke(project, ["--trash", "myproject.alpha"])

    assert outcome.exit_code == 0, outcome.output
    assert [call.args for call in trash.await_args_list] == [
        ("myproject", "alpha", project / "myproject-alpha", project)
    ]
    close.assert_not_awaited()
    assert "Moved feature/x to trash/feature/x." in outcome.output
    # The work is set aside, not parked: nothing is left to reopen.
    assert tasks == []


def test_a_refusal_fails_the_command_and_the_next_target_still_runs(project):
    outcome, trash, _, _ = _invoke(
        project,
        ["--trash", "myproject.alpha", "myproject.bravo"],
        results=(REFUSED, TRASHED),
    )

    assert outcome.exit_code == 1
    assert "trash/old is already in the trash" in outcome.output
    assert [call.args[1] for call in trash.await_args_list] == ["alpha", "bravo"]


@pytest.mark.parametrize("other", ["--force", "--discard", "--wait"])
def test_trash_excludes_the_other_ways_to_close(project, other):
    outcome, trash, close, _ = _invoke(project, ["--trash", other, "myproject.alpha"])

    assert outcome.exit_code == 2
    assert f"--trash cannot be used with {other}" in outcome.output
    trash.assert_not_awaited()
    close.assert_not_awaited()
