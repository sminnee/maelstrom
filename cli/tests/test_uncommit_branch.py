"""Tests for ``mael git uncommit-branch``.

The command runs with ``uncommit_branch`` mocked: these cover the output and
the exit code on a refusal. The real-git behaviour is tested in
``lib/domain/tests/test_worktree_uncommit.py``.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from mael_cli.cli import cli
from mael_domain.worktree_model import (
    CollapsedCommit,
    UncommitResult,
    WorktreeError,
)


class TestUncommitBranchCommand:
    """``mael git uncommit-branch`` reports what it did, or exits 1 on refusal."""

    def _run(self, worktree_path: Path, result=None, error=None):
        target = "mael_cli.git_cli.uncommit_branch"
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
            patched,
        ):
            return CliRunner().invoke(cli, ["git", "uncommit-branch"])

    def test_it_reports_the_base_the_ref_the_messages_and_the_stat(self, tmp_path):
        result = self._run(
            tmp_path,
            result=UncommitResult(
                base="feat/parent",
                history_ref="refs/mael/history/feat/child/20260908T121500Z",
                commits=2,
                stat=" one.txt | 1 +\n 1 file changed, 1 insertion(+)",
                collapsed=(
                    CollapsedCommit("a" * 40, "feat: one\n\nWhy one."),
                    CollapsedCommit("b" * 40, "feat: two"),
                ),
            ),
        )

        assert result.exit_code == 0
        assert result.output == (
            "Base: feat/parent\n"
            "Working history: refs/mael/history/feat/child/20260908T121500Z\n"
            "Uncommitted 2 commits into the working tree:\n"
            "\n"
            "- aaaaaaaaaaaa feat: one\n"
            "\n"
            "  Why one.\n"
            "- bbbbbbbbbbbb feat: two\n"
            "\n"
            " one.txt | 1 +\n"
            " 1 file changed, 1 insertion(+)\n"
        )

    def test_a_git_failure_exits_1_rather_than_raising(self, tmp_path):
        """The reads before the checks can fail on a broken worktree."""
        result = self._run(
            tmp_path,
            error=subprocess.CalledProcessError(
                128, "git", stderr="fatal: not a git repository"
            ),
        )

        assert result.exit_code == 1
        assert "not a git repository" in result.output

    def test_a_refusal_exits_1_with_the_message(self, tmp_path):
        result = self._run(tmp_path, error=WorktreeError("nothing to uncommit."))

        assert result.exit_code == 1
        assert "nothing to uncommit." in result.output
