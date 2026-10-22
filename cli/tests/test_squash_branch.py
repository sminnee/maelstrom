"""Tests for ``mael git squash-branch`` and the ``--remote``/``--local`` flags.

The command runs with ``squash_branch`` mocked: these cover the output, the
scope it passes, and the exit code on a refusal. The real-git behaviour is
tested in ``lib/domain/tests/test_worktree_squash.py``.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from mael_cli.cli import cli
from mael_domain.worktree_model import SquashResult, SquashScope, WorktreeError


class TestSquashBranchCommand:
    """``mael git squash-branch`` reports what it did, or exits 1 on refusal."""

    def _run(self, worktree_path: Path, args=None, result=None, error=None):
        target = "mael_cli.git_cli.squash_branch"
        patched = (
            patch(target, side_effect=error)
            if error is not None
            else patch(target, return_value=result)
        )
        with (
            patch(
                "mael_cli.git_cli.resolve_context",
                return_value=SimpleNamespace(worktree_path=worktree_path),
            ),
            patched as mock,
        ):
            invoked = CliRunner().invoke(cli, ["git", "squash-branch", *(args or [])])
        return invoked, mock

    def _result(self, scope: SquashScope = "remote"):
        return SquashResult(
            base="feat/parent",
            history_ref="refs/mael/history/feat/child/20260908T121500Z",
            commits=3,
            stat=" one.txt | 1 +\n 1 file changed, 1 insertion(+)",
            scope=scope,
            sha="abc1234",
            collapse_point="0000000",
        )

    def test_it_reports_the_base_the_ref_the_count_and_the_stat(self, tmp_path):
        invoked, _ = self._run(tmp_path, result=self._result())

        assert invoked.exit_code == 0
        assert invoked.output == (
            "Base: feat/parent\n"
            "Working history: refs/mael/history/feat/child/20260908T121500Z\n"
            "Squashed 3 commits into abc1234.\n"
            "\n"
            " one.txt | 1 +\n"
            " 1 file changed, 1 insertion(+)\n"
        )

    def test_it_defaults_to_the_remote_scope(self, tmp_path):
        _, mock = self._run(tmp_path, result=self._result())

        assert mock.call_args.kwargs["scope"] == "remote"

    def test_local_passes_the_local_scope(self, tmp_path):
        _, mock = self._run(tmp_path, args=["--local"], result=self._result("local"))

        assert mock.call_args.kwargs["scope"] == "local"

    def test_a_refusal_exits_1_with_the_message(self, tmp_path):
        invoked, _ = self._run(
            tmp_path, error=WorktreeError("every commit is already pushed.")
        )

        assert invoked.exit_code == 1
        assert "every commit is already pushed." in invoked.output

    def test_a_git_failure_exits_1_rather_than_raising(self, tmp_path):
        invoked, _ = self._run(
            tmp_path,
            error=subprocess.CalledProcessError(
                128, "git", stderr="fatal: not a git repository"
            ),
        )

        assert invoked.exit_code == 1
        assert "not a git repository" in invoked.output


class TestUncommitBranchCommandScope:
    """``mael git uncommit-branch`` takes the same flag pair."""

    def _run(self, worktree_path: Path, args=None):
        from mael_domain.worktree_model import UncommitResult

        with (
            patch(
                "mael_cli.git_cli.resolve_context",
                return_value=SimpleNamespace(worktree_path=worktree_path),
            ),
            patch(
                "mael_cli.git_cli.uncommit_branch",
                return_value=UncommitResult(
                    base="main",
                    history_ref="refs/mael/history/feature/work/20260908T121500Z",
                    commits=1,
                    stat="",
                ),
            ) as mock,
        ):
            CliRunner().invoke(cli, ["git", "uncommit-branch", *(args or [])])
        return mock

    def test_it_defaults_to_the_remote_scope(self, tmp_path):
        assert self._run(tmp_path).call_args.kwargs["scope"] == "remote"

    def test_local_passes_the_local_scope(self, tmp_path):
        mock = self._run(tmp_path, args=["--local"])

        assert mock.call_args.kwargs["scope"] == "local"
