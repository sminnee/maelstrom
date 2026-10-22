"""Tests for `mael close --force` and `--discard`, through ``CliRunner``.

The commands run with ``close_worktree`` mocked and an in-memory task table, so
these cover the reopen task and the flag checks. The real-git behaviour of
``force`` and ``discard`` is tested in ``lib/domain/tests/test_worktree_close.py``.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from mael_cli.cli import cli
from mael_domain.task import list_tasks
from mael_domain.task_table import InMemoryTaskTable
from mael_domain.worktree import CloseResult

# ---------------------------------------------------------------------------
# CLI: cmd_close --force via CliRunner (close_worktree mocked)
# ---------------------------------------------------------------------------


def _ctx():
    mock_ctx = MagicMock()
    mock_ctx.worktree = "alpha"
    mock_ctx.project = "myproject"
    mock_ctx.project_path = None  # skip copy-back-env path
    mock_ctx.worktree_path = MagicMock()
    mock_ctx.worktree_path.exists.return_value = True
    return mock_ctx


def _run(args, close_result, table=None):
    """Run ``mael close``, and return the result and the tasks it left."""
    runner = CliRunner()
    table = table or InMemoryTaskTable()

    async def task_table():
        return table

    with (
        patch("mael_cli.cli.resolve_context", return_value=_ctx()),
        patch("mael_domain.worktree_close.close_worktree", return_value=close_result),
        patch("mael_domain.worktree_close.get_env_status", return_value=[]),
        patch("mael_cli.cli.task_table", task_table),
    ):
        result = runner.invoke(cli, ["close", "myproject.alpha", *args])
    return result, asyncio.run(list_tasks(table, project="myproject"))


class TestCloseForceCli:
    """`mael close --force` task-creation behaviour via CliRunner."""

    def test_force_with_unmerged_work_creates_reopen_task(self, monkeypatch):
        close_result = CloseResult(
            success=True,
            message="Worktree closed (detached at origin/main)",
            branch="feature/work",
            had_unmerged_work=True,
        )
        # The task belongs to the branch, not to the chain of the session
        # that ran the close.
        monkeypatch.setenv("MAEL_TASK_PARENT", "some-chain")
        result, tasks = _run(["--force"], close_result)

        assert result.exit_code == 0
        [task] = tasks
        assert task.command == "reopen-branch"
        assert task.branch == "feature/work"
        assert task.parent == ""
        assert task.id in result.output

    def test_force_still_closes_when_the_task_store_fails(self):
        close_result = CloseResult(
            success=True,
            message="Worktree closed (detached at origin/main)",
            branch="feature/work",
            had_unmerged_work=True,
        )
        table = InMemoryTaskTable()
        with patch.object(table, "save", side_effect=OSError("disk full")):
            result, tasks = _run(["--force"], close_result, table)

        assert result.exit_code == 0
        assert "could not create reopen task for 'feature/work'" in result.output
        assert tasks == []

    def test_force_without_unmerged_work_no_task(self):
        close_result = CloseResult(
            success=True,
            message="Worktree closed (detached at origin/main)",
            branch="feature/work",
            had_unmerged_work=False,
        )
        result, tasks = _run(["--force"], close_result)

        assert result.exit_code == 0
        assert tasks == []

    def test_force_already_detached_branch_no_task(self):
        """A worktree already closed (branch == 'HEAD') gets no reopen task."""
        close_result = CloseResult(
            success=True,
            message="Worktree closed (detached at origin/main)",
            branch="HEAD",
            had_unmerged_work=True,
        )
        result, tasks = _run(["--force"], close_result)

        assert result.exit_code == 0
        assert tasks == []

    def test_force_threads_flag_into_close_worktree(self):
        close_result = CloseResult(
            success=True,
            message="Worktree closed (detached at origin/main)",
            branch="feature/work",
            had_unmerged_work=False,
        )
        runner = CliRunner()
        with (
            patch("mael_cli.cli.resolve_context", return_value=_ctx()),
            patch(
                "mael_domain.worktree_close.close_worktree", return_value=close_result
            ) as mock_close,
            patch("mael_domain.worktree_close.get_env_status", return_value=[]),
            patch("mael_cli.cli.task_table", AsyncMock()),
        ):
            runner.invoke(cli, ["close", "myproject.alpha", "--force"])
        _, kwargs = mock_close.call_args
        assert kwargs["force"] is True


class TestCloseDiscardCli:
    def test_discard_does_not_create_a_reopen_task(self):
        close_result = CloseResult(
            success=True,
            message="Worktree closed (detached at origin/main)",
            branch="feature/work",
            had_unmerged_work=True,
        )
        result, tasks = _run(["--discard"], close_result)

        assert result.exit_code == 0
        assert tasks == []

    def test_discard_cannot_be_combined_with_force(self):
        result, _ = _run(
            ["--discard", "--force"], CloseResult(success=True, message="Closed")
        )

        assert result.exit_code == 2
        assert "--discard cannot be used with --force" in result.output

    def test_discard_cannot_be_combined_with_wait(self):
        result, _ = _run(
            ["--discard", "--wait"], CloseResult(success=True, message="Closed")
        )

        assert result.exit_code == 2
        assert "--discard cannot be used with --wait" in result.output
