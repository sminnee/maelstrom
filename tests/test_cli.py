"""Tests for maelstrom.cli module."""

import dataclasses
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import ANY, patch, MagicMock

from click.testing import CliRunner

from maelstrom.cli import cli, _resolve_pr
from maelstrom.project_scaffold import scaffold_files
from maelstrom.worktree import SyncResult, WorktreeInfo, WorktreeSetup
from maelstrom.worktree_model import CopyBackResult


class TestResolvePr:
    """The PR cell resolves from the batch, falling back per branch.

    The batch is one GraphQL call for the repo. When it succeeds it is
    authoritative — a branch absent from it has no open PR, and must not cost a
    second network call. When it failed, every branch falls back, so a broken
    ``gh`` blanks no more than it did before the batch existed.
    """

    def test_a_hit_in_the_batch_needs_no_further_call(self):
        batch = {"feat/x": (42, 5)}
        with patch("maelstrom.cli.get_pr_number_and_commits") as per_branch:
            assert _resolve_pr(batch, Path("/p"), "feat/x") == (42, 5)
        per_branch.assert_not_called()

    def test_a_miss_in_a_good_batch_is_no_pr_not_a_lookup(self):
        with patch("maelstrom.cli.get_pr_number_and_commits") as per_branch:
            assert _resolve_pr({"other": (1, 1)}, Path("/p"), "feat/x") == (None, None)
        per_branch.assert_not_called()

    def test_an_empty_batch_still_answers_without_a_lookup(self):
        with patch("maelstrom.cli.get_pr_number_and_commits") as per_branch:
            assert _resolve_pr({}, Path("/p"), "feat/x") == (None, None)
        per_branch.assert_not_called()

    def test_a_failed_batch_falls_back_to_the_per_branch_call(self):
        with patch("maelstrom.cli.get_pr_number_and_commits", return_value=(7, 3)) as per_branch:
            assert _resolve_pr(None, Path("/p"), "feat/x") == (7, 3)
        per_branch.assert_called_once_with(Path("/p"), "feat/x")

    def test_a_detached_worktree_is_never_looked_up(self):
        """Both PR columns key on the branch name, so there is nothing to ask."""
        with patch("maelstrom.cli.get_pr_number_and_commits") as per_branch:
            assert _resolve_pr(None, Path("/p"), None) == (None, None)
        per_branch.assert_not_called()

    def test_list_all_reads_the_batch_not_one_call_per_row(self):
        """Guards the wiring, not just the helper.

        Every other test in this file patches the per-branch call, so a revert
        to per-row lookups would leave them all green. Here the batch answers
        and the per-branch call raises, so the batch must be what the command
        actually reads.
        """
        project_path = Path("/tmp/claude/projects/myproject")
        mock_wt = WorktreeInfo(
            path=project_path / "myproject-alpha",
            branch="feat/test",
            commit="abc123",
            is_dirty=False,
            commits_ahead=0,
        )

        def boom(*args, **kwargs):
            raise AssertionError("per-branch lookup used despite a good batch")

        with patch("maelstrom.cli.load_global_config") as mock_config:
            mock_config.return_value = MagicMock(projects_dir=Path("/tmp/claude/projects"))
            with patch("maelstrom.cli.find_all_projects", return_value=[project_path]), \
                 patch("maelstrom.cli.list_worktrees", return_value=[mock_wt]), \
                 patch("maelstrom.cli.closed_worktrees", return_value=set()), \
                 patch("maelstrom.cli.get_worktree_dirty_files", return_value=[]), \
                 patch("maelstrom.cli.get_local_only_commits", return_value=0), \
                 patch("maelstrom.cli.get_open_prs", return_value={"feat/test": (99, 7)}), \
                 patch("maelstrom.cli.get_pr_number_and_commits", side_effect=boom), \
                 patch("maelstrom.session_discovery.LiveSessionSet.count_for", return_value=0):
                result = CliRunner().invoke(cli, ["--json", "list-all"])

        assert result.exit_code == 0
        wt = json.loads(result.output)["projects"][0]["worktrees"][0]
        assert (wt["pr_number"], wt["pr_commits"]) == (99, 7)

    def test_a_project_with_no_branches_costs_no_network_call(self):
        """``list-all`` visits every project, most of which have nothing to ask.

        The PR lookup keys on the branch name, so a project whose worktrees are
        all detached has no question to put to GitHub. Asking anyway spends a
        round trip per project, which is what ``list-all`` has most of.
        """
        project_path = Path("/tmp/claude/projects/myproject")
        detached = WorktreeInfo(
            path=project_path / "myproject-alpha",
            branch="",
            commit="abc123",
            is_dirty=False,
            commits_ahead=0,
        )

        with patch("maelstrom.cli.load_global_config") as mock_config:
            mock_config.return_value = MagicMock(projects_dir=Path("/tmp/claude/projects"))
            with patch("maelstrom.cli.find_all_projects", return_value=[project_path]), \
                 patch("maelstrom.cli.list_worktrees", return_value=[detached]), \
                 patch("maelstrom.cli.closed_worktrees", return_value=set()), \
                 patch("maelstrom.cli.get_worktree_dirty_files", return_value=[]), \
                 patch("maelstrom.cli.get_local_only_commits", return_value=0), \
                 patch("maelstrom.cli.get_open_prs") as batch, \
                 patch("maelstrom.session_discovery.LiveSessionSet.count_for", return_value=0):
                result = CliRunner().invoke(cli, ["--json", "list-all"])

        assert result.exit_code == 0
        batch.assert_not_called()

    def test_the_batch_runs_once_per_project_not_once_per_worktree(self):
        """The whole point of batching: cost scales with projects, not rows."""
        project_path = Path("/tmp/claude/projects/myproject")
        worktrees = [
            WorktreeInfo(
                path=project_path / f"myproject-{name}",
                branch=f"feat/{name}",
                commit="abc123",
                is_dirty=False,
                commits_ahead=0,
            )
            for name in ("alpha", "bravo", "charlie")
        ]

        with patch("maelstrom.cli.load_global_config") as mock_config:
            mock_config.return_value = MagicMock(projects_dir=Path("/tmp/claude/projects"))
            with patch("maelstrom.cli.find_all_projects", return_value=[project_path]), \
                 patch("maelstrom.cli.list_worktrees", return_value=worktrees), \
                 patch("maelstrom.cli.closed_worktrees", return_value=set()), \
                 patch("maelstrom.cli.get_worktree_dirty_files", return_value=[]), \
                 patch("maelstrom.cli.get_local_only_commits", return_value=0), \
                 patch("maelstrom.cli.get_open_prs", return_value={}) as batch, \
                 patch("maelstrom.cli.get_pushed_commit_count", return_value=0), \
                 patch("maelstrom.session_discovery.LiveSessionSet.count_for", return_value=0):
                result = CliRunner().invoke(cli, ["--json", "list-all"])

        assert result.exit_code == 0
        assert batch.call_count == 1


def _sync_result(**overrides) -> SyncResult:
    """A successful open-flow SyncResult, with fields overridden as needed."""
    base = SyncResult(
        success=True,
        branch="feat-x",
        message="Successfully rebased feat-x onto origin/main",
    )
    return dataclasses.replace(base, **overrides) if overrides else base


def _failed_sync(message: str, **overrides) -> SyncResult:
    """A failed open-flow SyncResult. ``had_conflicts`` picks the caller's path."""
    return _sync_result(success=False, message=message, **overrides)


class TestListAllJson:
    """Tests for list-all command with --json flag."""

    def test_json_output_empty(self):
        """Test JSON output when no projects found."""
        runner = CliRunner()
        with patch("maelstrom.cli.load_global_config") as mock_config:
            mock_config.return_value = MagicMock(projects_dir=Path("/tmp/claude/projects"))
            with patch("maelstrom.cli.find_all_projects", return_value=[]):
                result = runner.invoke(cli, ["--json", "list-all"])
                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data == {"projects": []}

    def test_json_output_with_projects(self):
        """Test JSON output with project data."""
        runner = CliRunner()
        project_path = Path("/tmp/claude/projects/myproject")
        wt_path = project_path / "myproject-alpha"

        mock_wt = WorktreeInfo(
            path=wt_path,
            branch="feat/test",
            commit="abc123",
            is_dirty=False,
            commits_ahead=0,
        )

        with patch("maelstrom.cli.load_global_config") as mock_config:
            mock_config.return_value = MagicMock(projects_dir=Path("/tmp/claude/projects"))
            with patch("maelstrom.cli.find_all_projects", return_value=[project_path]):
                with patch("maelstrom.cli.list_worktrees", return_value=[mock_wt]):
                    with patch("maelstrom.cli.closed_worktrees", return_value=set()):
                        with patch("maelstrom.cli.get_worktree_dirty_files", return_value=["file.txt"]):
                            with patch("maelstrom.cli.get_local_only_commits", return_value=2):
                                with patch("maelstrom.cli.get_pr_number_and_commits", return_value=(42, 5)):
                                    with patch("maelstrom.session_discovery.LiveSessionSet.count_for", return_value=1):
                                        result = runner.invoke(cli, ["--json", "list-all"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["projects"]) == 1

        project = data["projects"][0]
        assert project["name"] == "myproject"
        assert project["path"] == str(project_path)
        assert len(project["worktrees"]) == 1

        wt = project["worktrees"][0]
        assert wt["name"] == "alpha"
        assert wt["folder"] == "myproject-alpha"
        assert wt["branch"] == "feat/test"
        assert wt["is_closed"] is False
        assert wt["dirty_files"] == 1
        assert wt["local_commits"] == 2
        assert wt["pr_number"] == 42
        assert wt["pr_commits"] == 5
        assert wt["session_count"] == 1

    def test_json_output_multiple_sessions(self):
        """A worktree with several live sessions reports the full count."""
        runner = CliRunner()
        project_path = Path("/tmp/claude/projects/myproject")
        wt_path = project_path / "myproject-alpha"

        mock_wt = WorktreeInfo(
            path=wt_path,
            branch="feat/test",
            commit="abc123",
            is_dirty=False,
            commits_ahead=0,
        )

        with patch("maelstrom.cli.load_global_config") as mock_config:
            mock_config.return_value = MagicMock(projects_dir=Path("/tmp/claude/projects"))
            with patch("maelstrom.cli.find_all_projects", return_value=[project_path]):
                with patch("maelstrom.cli.list_worktrees", return_value=[mock_wt]):
                    with patch("maelstrom.cli.closed_worktrees", return_value=set()):
                        with patch("maelstrom.cli.get_worktree_dirty_files", return_value=[]):
                            with patch("maelstrom.cli.get_local_only_commits", return_value=0):
                                with patch("maelstrom.cli.get_pr_number_and_commits", return_value=(None, None)):
                                    with patch("maelstrom.cli.get_pushed_commit_count", return_value=0):
                                        with patch("maelstrom.session_discovery.LiveSessionSet.count_for", return_value=3):
                                            result = runner.invoke(cli, ["--json", "list-all"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        wt = data["projects"][0]["worktrees"][0]
        assert wt["session_count"] == 3

    def test_json_output_closed_worktree(self):
        """Test JSON output for a closed worktree."""
        runner = CliRunner()
        project_path = Path("/tmp/claude/projects/myproject")
        wt_path = project_path / "myproject-bravo"

        mock_wt = WorktreeInfo(
            path=wt_path,
            branch="",
            commit="def456",
        )

        with patch("maelstrom.cli.load_global_config") as mock_config:
            mock_config.return_value = MagicMock(projects_dir=Path("/tmp/claude/projects"))
            with patch("maelstrom.cli.find_all_projects", return_value=[project_path]):
                with patch("maelstrom.cli.list_worktrees", return_value=[mock_wt]):
                    with patch("maelstrom.cli.closed_worktrees", return_value={wt_path}):
                        with patch("maelstrom.cli.get_worktree_dirty_files", return_value=[]):
                            with patch("maelstrom.session_discovery.LiveSessionSet.count_for", return_value=0):
                                result = runner.invoke(cli, ["--json", "list-all"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        wt = data["projects"][0]["worktrees"][0]
        assert wt["is_closed"] is True
        assert wt["branch"] is None
        assert wt["dirty_files"] == 0
        assert wt["local_commits"] == 0
        assert wt["session_count"] == 0

    def test_table_output_still_works(self):
        """Test that table output (without --json) still works."""
        runner = CliRunner()
        with patch("maelstrom.cli.load_global_config") as mock_config:
            mock_config.return_value = MagicMock(projects_dir=Path("/tmp/claude/projects"))
            with patch("maelstrom.cli.find_all_projects", return_value=[]):
                result = runner.invoke(cli, ["list-all"])
                assert result.exit_code == 0
                assert "No projects found." in result.output


class TestRemoveMultiTarget:
    """Tests for multi-target remove command."""

    def test_rm_multiple_worktrees(self):
        """Test that mael rm accepts multiple worktree arguments."""
        runner = CliRunner()
        project_path = Path("/tmp/claude/projects/myproject")

        with patch("maelstrom.cli.resolve_context") as mock_resolve:
            with patch("maelstrom.cli.get_worktree_dirty_files", return_value=[]):
                with patch("maelstrom.cli.remove_worktree_by_path") as mock_remove:
                    with patch("maelstrom.cli.get_env_status", return_value=None):
                        # Mock resolve_context for two different worktrees
                        def make_ctx(worktree_name):
                            ctx = MagicMock()
                            ctx.project = "myproject"
                            ctx.project_path = project_path
                            ctx.worktree = worktree_name
                            ctx.worktree_path = project_path / f"myproject-{worktree_name}"
                            return ctx

                        mock_resolve.side_effect = [make_ctx("alpha"), make_ctx("bravo")]

                        # Mock worktree paths to exist
                        with patch.object(Path, "exists", return_value=True):
                            result = runner.invoke(cli, ["rm", "alpha", "bravo"])

                        assert mock_remove.call_count == 2

    def test_rm_continues_on_error(self):
        """Test that rm continues processing after an error."""
        runner = CliRunner()
        project_path = Path("/tmp/claude/projects/myproject")

        with patch("maelstrom.cli.resolve_context") as mock_resolve:
            # First target raises error, second succeeds
            def make_ctx(worktree_name):
                ctx = MagicMock()
                ctx.project = "myproject"
                ctx.project_path = project_path
                ctx.worktree = worktree_name
                ctx.worktree_path = project_path / f"myproject-{worktree_name}"
                return ctx

            mock_resolve.side_effect = [
                ValueError("bad target"),
                make_ctx("bravo"),
            ]

            with patch("maelstrom.cli.get_worktree_dirty_files", return_value=[]):
                with patch("maelstrom.cli.remove_worktree_by_path"):
                    with patch("maelstrom.cli.get_env_status", return_value=None):
                        with patch.object(Path, "exists", return_value=True):
                            result = runner.invoke(cli, ["rm", "bad", "bravo"])

            # Should exit with error (one target failed)
            assert result.exit_code == 1
            assert "bad target" in result.output

    def test_rm_stops_running_env(self):
        """Test that mael rm stops a running environment before removing."""
        runner = CliRunner()
        project_path = Path("/tmp/claude/projects/myproject")

        with patch("maelstrom.cli.resolve_context") as mock_resolve:
            ctx = MagicMock()
            ctx.project = "myproject"
            ctx.project_path = project_path
            ctx.worktree = "alpha"
            ctx.worktree_path = project_path / "myproject-alpha"
            mock_resolve.return_value = ctx

            alive_service = MagicMock(alive=True)
            with patch("maelstrom.cli.get_worktree_dirty_files", return_value=[]), \
                 patch("maelstrom.cli.remove_worktree_by_path"), \
                 patch("maelstrom.cli.get_env_status", return_value=[alive_service]), \
                 patch("maelstrom.cli.stop_env", return_value=["web: stopped"]) as mock_stop, \
                 patch.object(Path, "exists", return_value=True):
                result = runner.invoke(cli, ["rm", "myproject.alpha"])

            mock_stop.assert_called_once_with(ANY, "myproject", "alpha")
            assert "Stopping environment" in result.output

    def test_rm_skips_stop_when_no_env(self):
        """Test that mael rm does not call stop_env when no environment is running."""
        runner = CliRunner()
        project_path = Path("/tmp/claude/projects/myproject")

        with patch("maelstrom.cli.resolve_context") as mock_resolve:
            ctx = MagicMock()
            ctx.project = "myproject"
            ctx.project_path = project_path
            ctx.worktree = "alpha"
            ctx.worktree_path = project_path / "myproject-alpha"
            mock_resolve.return_value = ctx

            with patch("maelstrom.cli.get_worktree_dirty_files", return_value=[]), \
                 patch("maelstrom.cli.remove_worktree_by_path"), \
                 patch("maelstrom.cli.get_env_status", return_value=None), \
                 patch("maelstrom.cli.stop_env") as mock_stop, \
                 patch.object(Path, "exists", return_value=True):
                result = runner.invoke(cli, ["rm", "myproject.alpha"])

            mock_stop.assert_not_called()


class TestCloseMultiTarget:
    """Tests for multi-target close command."""

    def test_close_no_args_uses_cwd(self):
        """Test that mael close with no args still uses cwd detection."""
        runner = CliRunner()

        with patch("maelstrom.cli.resolve_context") as mock_resolve:
            mock_ctx = MagicMock()
            mock_ctx.worktree = "alpha"
            mock_ctx.project = "myproject"
            mock_ctx.worktree_path = MagicMock()
            mock_ctx.worktree_path.exists.return_value = True
            mock_resolve.return_value = mock_ctx

            with patch("maelstrom.cli.copy_back_new_env_vars", return_value=CopyBackResult()), \
                 patch("maelstrom.cli.close_worktree") as mock_close, \
                 patch("maelstrom.cli.get_env_status", return_value=None):
                mock_close.return_value = MagicMock(success=True, message="Closed")
                result = runner.invoke(cli, ["close"])

            # Should have called resolve_context with None (cwd detection)
            mock_resolve.assert_called_once_with(
                None,
                require_project=True,
                require_worktree=True,
            )

    def test_close_multiple_worktrees(self):
        """Test that mael close accepts multiple worktree arguments."""
        runner = CliRunner()

        with patch("maelstrom.cli.resolve_context") as mock_resolve:
            def make_ctx(*args, **kwargs):
                ctx = MagicMock()
                ctx.worktree = args[0]
                ctx.project = "myproject"
                ctx.worktree_path = MagicMock()
                ctx.worktree_path.exists.return_value = True
                return ctx

            mock_resolve.side_effect = [make_ctx("alpha"), make_ctx("bravo")]

            with patch("maelstrom.cli.copy_back_new_env_vars", return_value=CopyBackResult()), \
                 patch("maelstrom.cli.close_worktree") as mock_close, \
                 patch("maelstrom.cli.get_env_status", return_value=None):
                mock_close.return_value = MagicMock(success=True, message="Closed")
                result = runner.invoke(cli, ["close", "alpha", "bravo"])

            assert mock_close.call_count == 2
            assert result.exit_code == 0

    def test_close_stops_running_env(self):
        """Test that mael close stops a running environment before closing."""
        runner = CliRunner()

        with patch("maelstrom.cli.resolve_context") as mock_resolve:
            mock_ctx = MagicMock()
            mock_ctx.worktree = "alpha"
            mock_ctx.project = "myproject"
            mock_ctx.worktree_path = MagicMock()
            mock_ctx.worktree_path.exists.return_value = True
            mock_resolve.return_value = mock_ctx

            alive_service = MagicMock(alive=True)
            with patch("maelstrom.cli.copy_back_new_env_vars", return_value=CopyBackResult()), \
                 patch("maelstrom.cli.close_worktree") as mock_close, \
                 patch("maelstrom.cli.get_env_status", return_value=[alive_service]), \
                 patch("maelstrom.cli.stop_env", return_value=["web: stopped"]) as mock_stop:
                mock_close.return_value = MagicMock(success=True, message="Closed")
                result = runner.invoke(cli, ["close", "myproject.alpha"])

            mock_stop.assert_called_once_with(ANY, "myproject", "alpha")
            assert "Stopping environment" in result.output

    def test_close_skips_stop_when_no_env(self):
        """Test that mael close does not call stop_env when no environment is running."""
        runner = CliRunner()

        with patch("maelstrom.cli.resolve_context") as mock_resolve:
            mock_ctx = MagicMock()
            mock_ctx.worktree = "alpha"
            mock_ctx.project = "myproject"
            mock_ctx.worktree_path = MagicMock()
            mock_ctx.worktree_path.exists.return_value = True
            mock_resolve.return_value = mock_ctx

            with patch("maelstrom.cli.copy_back_new_env_vars", return_value=CopyBackResult()), \
                 patch("maelstrom.cli.close_worktree") as mock_close, \
                 patch("maelstrom.cli.get_env_status", return_value=None), \
                 patch("maelstrom.cli.stop_env") as mock_stop:
                mock_close.return_value = MagicMock(success=True, message="Closed")
                result = runner.invoke(cli, ["close", "myproject.alpha"])

            mock_stop.assert_not_called()

    def test_close_closes_cmux_workspace(self):
        """Test that mael close closes the cmux workspace."""
        runner = CliRunner()

        with patch("maelstrom.cli.resolve_context") as mock_resolve:
            mock_ctx = MagicMock()
            mock_ctx.worktree = "alpha"
            mock_ctx.project = "myproject"
            mock_ctx.worktree_path = MagicMock()
            mock_ctx.worktree_path.exists.return_value = True
            mock_resolve.return_value = mock_ctx

            with patch("maelstrom.cli.copy_back_new_env_vars", return_value=CopyBackResult()), \
                 patch("maelstrom.cli.close_worktree") as mock_close, \
                 patch("maelstrom.cli.get_env_status", return_value=None), \
                 patch("maelstrom.cli.stop_env"), \
                 patch("maelstrom.cli.mael_layout.close_workspace", return_value=True) as mock_close_ws:
                mock_close.return_value = MagicMock(success=True, message="Closed")
                result = runner.invoke(cli, ["close", "myproject.alpha"])

            mock_close_ws.assert_called_once_with("myproject", "alpha")
            assert "Closed cmux workspace 'myproject-alpha'" in result.output

    def test_close_copies_back_new_var(self, tmp_path):
        """mael close copies a new worktree var back to the parent and reports it."""
        runner = CliRunner()

        project_path = tmp_path / "myproject"
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir(parents=True)
        (project_path / ".env").write_text("EXISTING=1\n")
        (worktree_path / ".env").write_text(
            "# Maelstrom port allocations\n"
            "WORKTREE=alpha\n"
            "# End Maelstrom port allocations\n"
            "\nEXISTING=1\nFOO=bar\n"
        )

        with patch("maelstrom.cli.resolve_context") as mock_resolve:
            mock_ctx = MagicMock()
            mock_ctx.worktree = "alpha"
            mock_ctx.project = "myproject"
            mock_ctx.project_path = project_path
            mock_ctx.worktree_path = worktree_path
            mock_resolve.return_value = mock_ctx

            with patch("maelstrom.cli.close_worktree") as mock_close, \
                 patch("maelstrom.cli.get_env_status", return_value=None):
                mock_close.return_value = MagicMock(success=True, message="Closed")
                result = runner.invoke(cli, ["close", "myproject.alpha"])

        assert result.exit_code == 0, result.output
        assert "Copied 1 new var(s) back" in result.output
        assert "+FOO=bar" in result.output
        assert "FOO=bar" in (project_path / ".env").read_text()

    def test_close_does_not_fail_on_conflict(self, tmp_path):
        """A copy-back conflict warns but does not fail the close."""
        runner = CliRunner()

        project_path = tmp_path / "myproject"
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir(parents=True)
        parent_text = "FOO=parentval\nBAR=parentbar\n"
        (project_path / ".env").write_text(parent_text)
        (worktree_path / ".env").write_text(
            "# Maelstrom port allocations\n"
            "WORKTREE=alpha\n"
            "# End Maelstrom port allocations\n"
            "\nFOO=wtval\nBAR=wtbar\n"
        )

        with patch("maelstrom.cli.resolve_context") as mock_resolve:
            mock_ctx = MagicMock()
            mock_ctx.worktree = "alpha"
            mock_ctx.project = "myproject"
            mock_ctx.project_path = project_path
            mock_ctx.worktree_path = worktree_path
            mock_resolve.return_value = mock_ctx

            with patch("maelstrom.cli.close_worktree") as mock_close, \
                 patch("maelstrom.cli.get_env_status", return_value=None):
                mock_close.return_value = MagicMock(success=True, message="Closed")
                result = runner.invoke(cli, ["close", "myproject.alpha"])

        assert result.exit_code == 0, result.output
        # One consolidated warning listing both keys, with a synthetic diff:
        # the worktree value (-, overwritten) vs the resolved parent value (+).
        assert "FOO, BAR differ between worktree" in result.output
        assert "-FOO=wtval" in result.output
        assert "+FOO=parentval" in result.output
        assert "-BAR=wtbar" in result.output
        assert "+BAR=parentbar" in result.output
        # Parent value untouched.
        assert (project_path / ".env").read_text() == parent_text
        mock_close.assert_called_once()


class TestCloseWait:
    """Tests for `mael close --wait`."""

    def _ctx(self):
        mock_ctx = MagicMock()
        mock_ctx.worktree = "alpha"
        mock_ctx.project = "myproject"
        mock_ctx.worktree_path = MagicMock()
        mock_ctx.worktree_path.exists.return_value = True
        return mock_ctx

    def test_wait_merged_proceeds_to_close(self):
        """--wait calls wait_for_merge and, once merged, runs close_worktree."""
        runner = CliRunner()

        with patch("maelstrom.cli.resolve_context", return_value=self._ctx()), \
             patch("maelstrom.cli.copy_back_new_env_vars", return_value=CopyBackResult()), \
             patch("maelstrom.cli.get_env_status", return_value=None), \
             patch("maelstrom.cli.wait_for_merge") as mock_wait, \
             patch("maelstrom.cli.close_worktree") as mock_close:
            mock_wait.return_value = MagicMock(number=42)
            mock_close.return_value = MagicMock(success=True, message="Closed")
            result = runner.invoke(cli, ["close", "myproject.alpha", "--wait"])

        mock_wait.assert_called_once()
        mock_close.assert_called_once()
        assert "PR #42 merged." in result.output
        assert result.exit_code == 0

    def test_wait_passes_timeout_and_interval(self):
        """--timeout/--interval are forwarded to wait_for_merge."""
        runner = CliRunner()

        with patch("maelstrom.cli.resolve_context", return_value=self._ctx()), \
             patch("maelstrom.cli.copy_back_new_env_vars", return_value=CopyBackResult()), \
             patch("maelstrom.cli.get_env_status", return_value=None), \
             patch("maelstrom.cli.wait_for_merge") as mock_wait, \
             patch("maelstrom.cli.close_worktree") as mock_close:
            mock_wait.return_value = MagicMock(number=1)
            mock_close.return_value = MagicMock(success=True, message="Closed")
            runner.invoke(
                cli,
                ["close", "myproject.alpha", "--wait", "--timeout", "120", "--interval", "5"],
            )

        _, kwargs = mock_wait.call_args
        assert kwargs["timeout"] == 120
        assert kwargs["poll_interval"] == 5

    def test_wait_runtime_error_skips_close(self):
        """A RuntimeError (closed-unmerged / red CI) skips close and exits 1."""
        runner = CliRunner()

        with patch("maelstrom.cli.resolve_context", return_value=self._ctx()), \
             patch("maelstrom.cli.copy_back_new_env_vars", return_value=CopyBackResult()), \
             patch("maelstrom.cli.get_env_status", return_value=None), \
             patch("maelstrom.cli.wait_for_merge",
                   side_effect=RuntimeError("PR #7 was closed without merging")), \
             patch("maelstrom.cli.close_worktree") as mock_close:
            result = runner.invoke(cli, ["close", "myproject.alpha", "--wait"])

        mock_close.assert_not_called()
        assert result.exit_code == 1
        assert "closed without merging" in result.output

    def test_wait_timeout_skips_close(self):
        """A TimeoutError skips close and exits 1."""
        runner = CliRunner()

        with patch("maelstrom.cli.resolve_context", return_value=self._ctx()), \
             patch("maelstrom.cli.copy_back_new_env_vars", return_value=CopyBackResult()), \
             patch("maelstrom.cli.get_env_status", return_value=None), \
             patch("maelstrom.cli.wait_for_merge",
                   side_effect=TimeoutError("Timed out after 3600s")), \
             patch("maelstrom.cli.close_worktree") as mock_close:
            result = runner.invoke(cli, ["close", "myproject.alpha", "--wait"])

        mock_close.assert_not_called()
        assert result.exit_code == 1
        assert "Timed out" in result.output

    def test_no_wait_does_not_poll(self):
        """Without --wait, wait_for_merge is never called."""
        runner = CliRunner()

        with patch("maelstrom.cli.resolve_context", return_value=self._ctx()), \
             patch("maelstrom.cli.copy_back_new_env_vars", return_value=CopyBackResult()), \
             patch("maelstrom.cli.get_env_status", return_value=None), \
             patch("maelstrom.cli.wait_for_merge") as mock_wait, \
             patch("maelstrom.cli.close_worktree") as mock_close:
            mock_close.return_value = MagicMock(success=True, message="Closed")
            result = runner.invoke(cli, ["close", "myproject.alpha"])

        mock_wait.assert_not_called()
        assert result.exit_code == 0


class TestStaleSymlinkCleanup:
    """Tests for stale symlink cleanup in _symlink_items."""

    def test_removes_stale_symlink_into_source(self, tmp_path):
        """Stale symlinks pointing into source_dir are removed."""
        from maelstrom.claude_integration import _symlink_items

        source = tmp_path / "source"
        source.mkdir()
        target = tmp_path / "target"
        target.mkdir()

        # Create a symlink in target that points to a non-existent file in source
        stale = target / "old_command"
        stale.symlink_to(source / "removed_file")

        messages = _symlink_items(source, target)

        assert not stale.exists()
        assert not stale.is_symlink()
        assert any("Removed stale link old_command" in m for m in messages)

    def test_preserves_foreign_symlinks(self, tmp_path):
        """Symlinks pointing outside source_dir are not touched."""
        from maelstrom.claude_integration import _symlink_items

        source = tmp_path / "source"
        source.mkdir()
        target = tmp_path / "target"
        target.mkdir()
        other = tmp_path / "other"
        other.mkdir()

        # Create a foreign symlink (points outside source)
        foreign_target = other / "some_file"
        foreign_target.touch()
        foreign = target / "foreign_link"
        foreign.symlink_to(foreign_target)

        messages = _symlink_items(source, target)

        assert foreign.is_symlink()
        assert not any("foreign_link" in m for m in messages)

    def test_preserves_valid_symlinks(self, tmp_path):
        """Valid symlinks into source_dir are preserved."""
        from maelstrom.claude_integration import _symlink_items

        source = tmp_path / "source"
        source.mkdir()
        target = tmp_path / "target"
        target.mkdir()

        # Create a file in source and a valid symlink to it
        (source / "valid_file").touch()
        valid = target / "valid_file"
        valid.symlink_to(source / "valid_file")

        messages = _symlink_items(source, target)

        assert valid.is_symlink()
        assert not any("Removed" in m for m in messages)


class TestCmdAddRecycle:
    """Tests for `mael add` recycle path triggering env regeneration/restart."""

    def _setup_recycle_mocks(self, stack, tmp_path, helper_return=([], None)):
        """Patch the recycle path of cmd_add. Returns the helper mock."""
        from contextlib import ExitStack

        project_path = tmp_path / "proj"
        project_path.mkdir()
        worktree_path = tmp_path / "proj-bravo"
        worktree_path.mkdir()

        ctx = MagicMock(
            project="proj",
            project_path=project_path,
            worktree=None,
            worktree_path=None,
        )

        closed_wt = MagicMock(path=worktree_path)

        stack.enter_context(patch("maelstrom.cli.resolve_context", return_value=ctx))
        # The recycle collaborators now run inside worktree.setup_worktree_for_branch.
        stack.enter_context(patch(
            "maelstrom.worktree.find_worktree_by_branch", return_value=None,
        ))
        stack.enter_context(patch("maelstrom.worktree.find_closed_worktree", return_value=closed_wt))
        stack.enter_context(patch("maelstrom.worktree.recycle_worktree", return_value=worktree_path))
        stack.enter_context(patch(
            "maelstrom.worktree.extract_worktree_name_from_folder", return_value="bravo",
        ))
        stack.enter_context(patch("maelstrom.worktree.reclaim_or_allocate_ports"))
        stack.enter_context(patch("maelstrom.worktree.setup_claude_memory_symlink"))
        stack.enter_context(patch("maelstrom.worktree.update_claude_local_md", return_value=False))
        stack.enter_context(patch("maelstrom.worktree.run_install_cmd"))
        # An opened worktree is synced before finalize; these mocks have no real git.
        stack.enter_context(patch(
            "maelstrom.worktree.sync_worktree_with_autorepair",
            return_value=_sync_result(),
        ))
        # The recycle env block stays CLI-side and derives the NATO name there too.
        stack.enter_context(patch(
            "maelstrom.cli.extract_worktree_name_from_folder", return_value="bravo",
        ))
        stack.enter_context(patch("maelstrom.cli.launch_claude_in_worktree"))

        helper = stack.enter_context(patch(
            "maelstrom.cli.regenerate_and_restart_if_running",
            return_value=helper_return,
        ))
        return helper, project_path, worktree_path

    def test_recycle_invokes_helper(self, tmp_path):
        """The recycle branch calls regenerate_and_restart_if_running with NATO name."""
        from contextlib import ExitStack

        with ExitStack() as stack:
            helper, project_path, worktree_path = self._setup_recycle_mocks(
                stack, tmp_path,
            )

            runner = CliRunner()
            result = runner.invoke(cli, ["add", "feat-x"])
            assert result.exit_code == 0, result.output
            helper.assert_called_once_with(
                ANY, "proj", "bravo", project_path, worktree_path,
            )
            assert "Regenerated .env for proj/bravo." in result.output

    def test_recycle_running_env_emits_stop_and_status(self, tmp_path):
        """When env was running, prints stop messages and invokes status display."""
        from contextlib import ExitStack

        new_state = MagicMock()
        with ExitStack() as stack:
            helper, project_path, worktree_path = self._setup_recycle_mocks(
                stack, tmp_path,
                helper_return=(["web (pid 100): stopped"], new_state),
            )
            ensure_browser = stack.enter_context(patch(
                "maelstrom.cli.ensure_cmux_browser",
            ))
            print_status = stack.enter_context(patch(
                "maelstrom.cli.print_service_status",
            ))

            runner = CliRunner()
            result = runner.invoke(cli, ["add", "feat-x"])
            assert result.exit_code == 0, result.output
            assert "web (pid 100): stopped" in result.output
            assert "Environment stopped for proj/bravo." in result.output
            ensure_browser.assert_called_once_with(new_state, project_path, "bravo")
            print_status.assert_called_once_with("proj", "bravo", project_path)


class TestCmdAddExistingBranch:
    """Tests for `mael add <branch>` when the branch is already checked out."""

    def _setup(self, stack, tmp_path, existing=True):
        """Patch cmd_add so an existing worktree is (or isn't) found.

        cmd_add defers cmux placement to launch_claude_in_worktree (mocked
        here), so the cmux layers are never reached and need no patching.
        Returns (existing_wt_path, mocks dict).
        """
        project_path = tmp_path / "proj"
        project_path.mkdir()
        worktree_path = tmp_path / "proj-bravo"
        worktree_path.mkdir()

        ctx = MagicMock(
            project="proj",
            project_path=project_path,
            worktree=None,
            worktree_path=None,
        )

        stack.enter_context(patch("maelstrom.cli.resolve_context", return_value=ctx))
        # cmd_add now defers entirely to the shared launcher for reuse: it always
        # calls setup_worktree_for_branch (the core fn) then launch_claude_in_worktree.
        # The core fn reads find_worktree_by_branch / extract_worktree_name_from_folder
        # from the worktree namespace.
        stack.enter_context(patch(
            "maelstrom.worktree.find_worktree_by_branch",
            return_value=worktree_path if existing else None,
        ))
        stack.enter_context(patch(
            "maelstrom.worktree.extract_worktree_name_from_folder", return_value="bravo",
        ))
        # An opened worktree is synced before finalize; these mocks have no real git.
        stack.enter_context(patch(
            "maelstrom.worktree.sync_worktree_with_autorepair",
            return_value=_sync_result(),
        ))

        mocks = {
            "create_worktree": stack.enter_context(
                patch("maelstrom.worktree.create_worktree", return_value=worktree_path)
            ),
            "run_install_cmd": stack.enter_context(patch("maelstrom.worktree.run_install_cmd")),
            "launch_claude_in_worktree": stack.enter_context(
                patch("maelstrom.cli.launch_claude_in_worktree")
            ),
            "find_closed_worktree": stack.enter_context(
                patch("maelstrom.worktree.find_closed_worktree", return_value=None)
            ),
            "update_claude_local_md": stack.enter_context(
                patch("maelstrom.worktree.update_claude_local_md", return_value=False)
            ),
        }
        return worktree_path, mocks

    def test_existing_worktree_reuses_via_launcher(self, tmp_path):
        """Existing worktree → reused (no git touch); the launcher places it.

        Reuse-as-tab now lives entirely in the shared launcher
        (launch_claude_in_worktree), so cmd_add just hands the reused worktree
        to it — it never touches git/install itself.
        """
        from contextlib import ExitStack

        with ExitStack() as stack:
            existing_wt, mocks = self._setup(stack, tmp_path)

            result = CliRunner().invoke(cli, ["add", "feat-x"])
            assert result.exit_code == 0, result.output

            mocks["launch_claude_in_worktree"].assert_called_once_with(
                existing_wt, project="proj", worktree="bravo",
            )
            mocks["create_worktree"].assert_not_called()
            # cmd_add no longer runs install itself; the launcher owns it.
            mocks["run_install_cmd"].assert_not_called()

    def test_not_in_cmux_starts_session(self, tmp_path):
        """Not in cmux + existing worktree → reused, no create_worktree."""
        from contextlib import ExitStack

        with ExitStack() as stack:
            existing_wt, mocks = self._setup(stack, tmp_path)

            result = CliRunner().invoke(cli, ["add", "feat-x"])
            assert result.exit_code == 0, result.output

            mocks["launch_claude_in_worktree"].assert_called_once_with(
                existing_wt, project="proj", worktree="bravo",
            )
            mocks["create_worktree"].assert_not_called()

    def test_no_existing_worktree_creates(self, tmp_path):
        """No existing worktree → falls through to the create path (regression)."""
        from contextlib import ExitStack

        with ExitStack() as stack:
            _, mocks = self._setup(stack, tmp_path, existing=False)

            result = CliRunner().invoke(cli, ["add", "feat-x"])
            assert result.exit_code == 0, result.output

            mocks["create_worktree"].assert_called_once()
            # cmd_add defers install to the launcher (run_install=False).
            mocks["run_install_cmd"].assert_not_called()
            # The create echo names the worktree.
            assert "→ proj/bravo (created)" in result.output


class TestCmdAddSync:
    """`mael add` reports the open-flow sync, and blocks the launch when it fails."""

    def _run(self, tmp_path, sync):
        """Invoke `mael add feat-x` with a stubbed setup carrying ``sync``."""
        from contextlib import ExitStack

        project_path = tmp_path / "proj"
        project_path.mkdir()
        worktree_path = tmp_path / "proj-bravo"
        worktree_path.mkdir()
        ctx = MagicMock(
            project="proj", project_path=project_path,
            worktree=None, worktree_path=None,
        )

        with ExitStack() as stack:
            stack.enter_context(patch("maelstrom.cli.resolve_context", return_value=ctx))
            stack.enter_context(patch(
                "maelstrom.cli.setup_worktree_for_branch",
                return_value=WorktreeSetup(
                    path=worktree_path, name="bravo", action="created", sync=sync,
                ),
            ))
            stack.enter_context(patch("maelstrom.cli.get_app_url", return_value=None))
            launch = stack.enter_context(patch("maelstrom.cli.launch_claude_in_worktree"))
            result = CliRunner().invoke(cli, ["add", "feat-x"])
        return result, launch

    def test_aborted_conflict_blocks_the_launch(self, tmp_path):
        sync = _failed_sync(
            "Autorepair did not complete the rebase; aborted and restored.",
            had_conflicts=True,
            aborted=True,
        )
        result, launch = self._run(tmp_path, sync)

        assert result.exit_code == 1
        assert "aborted and restored" in result.output
        assert "mael sync --autorepair" in result.output
        launch.assert_not_called()

    def test_fetch_failure_blocks_the_launch(self, tmp_path):
        sync = _failed_sync("Failed to fetch from origin: no route")
        result, launch = self._run(tmp_path, sync)

        assert result.exit_code != 0
        assert "Sync failed" in result.output
        launch.assert_not_called()

    def test_successful_sync_is_reported_and_the_launch_proceeds(self, tmp_path):
        sync = _sync_result(repaired=True, pushed=True, push_message="Pushed feat-x to origin")
        result, launch = self._run(tmp_path, sync)

        assert result.exit_code == 0, result.output
        assert "Successfully rebased feat-x onto origin/main" in result.output
        assert "Pushed feat-x to origin" in result.output
        assert "resolved by a headless Claude session" in result.output
        launch.assert_called_once()

    def test_reused_worktree_reports_no_sync(self, tmp_path):
        """``sync is None`` (the reuse path) prints nothing and never blocks."""
        result, launch = self._run(tmp_path, None)

        assert result.exit_code == 0, result.output
        assert "rebased" not in result.output
        launch.assert_called_once()


class TestCmdSyncAutorepair:
    """`mael sync --autorepair` routing and reporting."""

    def _run(self, args, sync_result, tmp_path):
        from contextlib import ExitStack

        worktree_path = tmp_path / "proj-bravo"
        worktree_path.mkdir()
        ctx = MagicMock(project="proj", worktree="bravo", worktree_path=worktree_path)

        with ExitStack() as stack:
            stack.enter_context(patch("maelstrom.cli.resolve_context", return_value=ctx))
            plain = stack.enter_context(patch("maelstrom.cli.sync_worktree"))
            repair = stack.enter_context(patch(
                "maelstrom.cli.sync_worktree_with_autorepair", return_value=sync_result,
            ))
            plain.return_value = sync_result
            result = CliRunner().invoke(cli, ["sync", *args])
        return result, plain, repair

    def test_flag_routes_to_the_autorepair_sync(self, tmp_path):
        result, plain, repair = self._run(["--autorepair"], _sync_result(), tmp_path)

        assert result.exit_code == 0, result.output
        repair.assert_called_once()
        plain.assert_not_called()

    def test_without_the_flag_the_plain_sync_runs(self, tmp_path):
        result, plain, repair = self._run([], _sync_result(), tmp_path)

        assert result.exit_code == 0, result.output
        plain.assert_called_once()
        repair.assert_not_called()

    def test_repaired_success_says_so(self, tmp_path):
        result, _, _ = self._run(["--autorepair"], _sync_result(repaired=True), tmp_path)

        assert result.exit_code == 0, result.output
        assert "resolved by a headless Claude session" in result.output

    def test_failure_exits_non_zero_without_conflict_help(self, tmp_path):
        sync = _failed_sync(
            "Autorepair did not complete the rebase; aborted and restored.",
            had_conflicts=True,
            aborted=True,
        )
        result, _, _ = self._run(["--autorepair"], sync, tmp_path)

        assert result.exit_code == 1
        assert "aborted and restored" in result.output
        # No mid-rebase state remains, so the manual-resolution help is not printed.
        assert "git rebase --continue" not in result.output

    def test_a_failure_that_left_a_rebase_still_prints_the_manual_help(self, tmp_path):
        """Not every autorepair failure aborts.

        A session that finished the rebase on the wrong branch leaves work for a
        human, so the resolution steps must survive.
        """
        sync = _failed_sync(
            "Autorepair finished the rebase but left the worktree on other.",
            had_conflicts=True,
            aborted=False,
        )
        result, _, _ = self._run(["--autorepair"], sync, tmp_path)

        assert result.exit_code == 1
        assert "git rebase --continue" in result.output

    def test_sync_all_flag_routes_to_the_autorepair_sync(self, tmp_path):
        """`mael sync-all --autorepair` repairs each worktree it sweeps."""
        from contextlib import ExitStack

        project_path = tmp_path / "proj"
        wt_path = project_path / "proj-bravo"
        wt_path.mkdir(parents=True)
        ctx = MagicMock(project="proj", project_path=project_path)

        with ExitStack() as stack:
            stack.enter_context(patch("maelstrom.cli.resolve_context", return_value=ctx))
            stack.enter_context(patch(
                "maelstrom.cli.list_worktrees",
                return_value=[MagicMock(path=wt_path, branch="feature/work")],
            ))
            stack.enter_context(patch("maelstrom.cli.run_git"))
            stack.enter_context(patch("maelstrom.worktree.update_local_main"))
            plain = stack.enter_context(patch("maelstrom.cli.sync_worktree"))
            repair = stack.enter_context(patch(
                "maelstrom.cli.sync_worktree_with_autorepair",
                return_value=_sync_result(repaired=True),
            ))
            result = CliRunner().invoke(cli, ["sync-all", "--autorepair"])

        assert result.exit_code == 0, result.output
        repair.assert_called_once()
        plain.assert_not_called()
        assert "resolved by a headless Claude session" in result.output

    def test_sync_all_without_the_flag_uses_the_plain_sync(self, tmp_path):
        from contextlib import ExitStack

        project_path = tmp_path / "proj"
        wt_path = project_path / "proj-bravo"
        wt_path.mkdir(parents=True)
        ctx = MagicMock(project="proj", project_path=project_path)

        with ExitStack() as stack:
            stack.enter_context(patch("maelstrom.cli.resolve_context", return_value=ctx))
            stack.enter_context(patch(
                "maelstrom.cli.list_worktrees",
                return_value=[MagicMock(path=wt_path, branch="feature/work")],
            ))
            stack.enter_context(patch("maelstrom.cli.run_git"))
            stack.enter_context(patch("maelstrom.worktree.update_local_main"))
            plain = stack.enter_context(patch(
                "maelstrom.cli.sync_worktree", return_value=_sync_result(),
            ))
            repair = stack.enter_context(patch("maelstrom.cli.sync_worktree_with_autorepair"))
            result = CliRunner().invoke(cli, ["sync-all"])

        assert result.exit_code == 0, result.output
        plain.assert_called_once()
        repair.assert_not_called()

    def test_the_start_of_an_autorepair_reaches_the_terminal(self, tmp_path):
        """The model layer stays click-free, so it announces with bare print.

        Ordering and wording are covered at the model layer, in test_sync_flags.
        The one thing only this layer can show is that a non-click.echo line
        still reaches the user through Click's output machinery.
        """
        from contextlib import ExitStack

        worktree_path = tmp_path / "proj-bravo"
        worktree_path.mkdir()
        ctx = MagicMock(project="proj", worktree="bravo", worktree_path=worktree_path)

        with ExitStack() as stack:
            stack.enter_context(patch("maelstrom.cli.resolve_context", return_value=ctx))
            # Stop after the announcement: a conflicted rebase is set up in
            # test_sync_flags, and this asserts only that the line gets out.
            stack.enter_context(patch(
                "maelstrom.worktree.sync_worktree",
                return_value=_failed_sync("Rebase hit conflicts", had_conflicts=True),
            ))
            stack.enter_context(patch(
                "maelstrom.worktree.rebase_in_progress", return_value=True,
            ))
            stack.enter_context(patch(
                "maelstrom.worktree.run_resolve_rebase_session",
                side_effect=OSError("claude: not found"),
            ))
            stack.enter_context(patch("maelstrom.worktree._abort_rebase"))
            result = CliRunner().invoke(cli, ["sync", "--autorepair"])

        assert "Starting autorepair" in result.output


class TestClaudePlacementFailure:
    """`mael claude` / `mael open` cmux-or-error behaviour.

    With no local-execvp fallback, a failed cmux placement must raise a clear
    ClickException — never silently run `claude` in the current shell.
    """

    def _ctx(self, tmp_path):
        wt = tmp_path / "proj-bravo"
        wt.mkdir()
        return MagicMock(
            project="proj",
            worktree="bravo",
            worktree_path=wt,
        )

    def test_claude_raises_when_placement_fails(self, tmp_path):
        from contextlib import ExitStack

        with ExitStack() as stack:
            stack.enter_context(
                patch("maelstrom.cli.resolve_context", return_value=self._ctx(tmp_path))
            )
            stack.enter_context(patch(
                "maelstrom.cli.launch_claude_in_worktree", return_value=False,
            ))
            result = CliRunner().invoke(cli, ["claude", "proj.bravo"])
            assert result.exit_code != 0
            assert "cmux is not running" in result.output

    def test_claude_succeeds_when_placed(self, tmp_path):
        from contextlib import ExitStack

        with ExitStack() as stack:
            stack.enter_context(
                patch("maelstrom.cli.resolve_context", return_value=self._ctx(tmp_path))
            )
            launch = stack.enter_context(patch(
                "maelstrom.cli.launch_claude_in_worktree", return_value=True,
            ))
            result = CliRunner().invoke(cli, ["claude", "proj.bravo"])
            assert result.exit_code == 0, result.output
            launch.assert_called_once()


class TestCmuxStatus:
    """`mael cmux status` reports whether ensure_cmux_running succeeds."""

    def test_ok_when_cmux_reachable(self):
        with patch(
            "maelstrom.cli.ensure_cmux_running", return_value=True
        ), patch.dict(os.environ, {"CMUX_SOCKET_PATH": "/tmp/c.sock"}):
            result = CliRunner().invoke(cli, ["cmux", "status"])
            assert result.exit_code == 0, result.output
            assert "cmux OK" in result.output
            assert "/tmp/c.sock" in result.output

    def test_errors_when_cmux_unreachable(self):
        with patch(
            "maelstrom.cli.ensure_cmux_running", return_value=False
        ):
            result = CliRunner().invoke(cli, ["cmux", "status"])
            assert result.exit_code != 0
            assert "not reachable" in result.output


class TestCreateProject:
    """Tests for `mael create-project`."""

    def _invoke(self, args, tmp_path, *, url="git@github.com:me/proj.git",
                add_project_error=None):
        """Run create-project with the remote and checkout halves mocked."""
        with patch("maelstrom.cli.load_global_config") as mock_config, \
             patch("maelstrom.cli.create_project_repo", return_value=url) as mock_create, \
             patch("maelstrom.cli.add_project") as mock_add_project, \
             patch("maelstrom.cli.cmd_add") as mock_add:
            mock_config.return_value = MagicMock(projects_dir=tmp_path)
            mock_add_project.return_value = tmp_path / "proj"
            if add_project_error is not None:
                mock_add_project.side_effect = add_project_error
            result = CliRunner().invoke(cli, ["create-project"] + args)
        return result, mock_create, mock_add_project, mock_add

    def test_threads_the_url_from_repo_creation_into_checkout(self, tmp_path):
        result, mock_create, mock_add_project, _ = self._invoke(["proj"], tmp_path)

        assert result.exit_code == 0, result.output
        mock_create.assert_called_once_with("proj", private=True, description=None)
        assert mock_add_project.call_args[0][0] == "git@github.com:me/proj.git"

    def test_public_flag_creates_a_public_repo(self, tmp_path):
        _, mock_create, _, _ = self._invoke(["proj", "--public"], tmp_path)
        assert mock_create.call_args.kwargs["private"] is False

    def test_description_is_passed_through(self, tmp_path):
        _, mock_create, _, _ = self._invoke(
            ["proj", "--description", "A thing"], tmp_path
        )
        assert mock_create.call_args.kwargs["description"] == "A thing"

    def test_invalid_name_exits_before_touching_github(self, tmp_path):
        result, mock_create, _, _ = self._invoke(["foo.bar"], tmp_path)

        assert result.exit_code != 0
        assert "cannot contain dots" in result.output
        mock_create.assert_not_called()

    def test_existing_project_dir_fails_before_touching_github(self, tmp_path):
        (tmp_path / "proj").mkdir()
        result, mock_create, _, _ = self._invoke(
            ["proj", "--projects-dir", str(tmp_path)], tmp_path
        )

        assert result.exit_code != 0
        assert "already exists" in result.output
        mock_create.assert_not_called()

    def test_checkout_failure_reports_the_url_and_the_recovery(self, tmp_path):
        result, _, _, _ = self._invoke(
            ["proj"], tmp_path, add_project_error=RuntimeError("clone failed")
        )

        assert result.exit_code != 0
        assert "git@github.com:me/proj.git" in result.output
        assert "mael add-project" in result.output

    def test_checkout_failure_reports_the_git_error_not_just_the_exit_code(
        self, tmp_path
    ):
        """A failed git call keeps its reason in stderr, not in str(e)."""
        err = subprocess.CalledProcessError(
            128, ["git", "clone"], stderr="fatal: repository not found\n"
        )
        result, _, _, _ = self._invoke(["proj"], tmp_path, add_project_error=err)

        assert result.exit_code != 0
        assert "fatal: repository not found" in result.output

    def test_custom_projects_dir_skips_the_worktree_step(self, tmp_path):
        """`mael add` resolves via the global projects_dir, so it cannot be used."""
        other = tmp_path / "elsewhere"
        other.mkdir()
        result, _, _, mock_add = self._invoke(
            ["proj", "--projects-dir", str(other)], tmp_path
        )

        assert result.exit_code == 0, result.output
        mock_add.assert_not_called()
        assert "No worktree opened" in result.output

    def test_projects_dir_naming_the_configured_one_still_opens_a_worktree(
        self, tmp_path
    ):
        """An equivalent path (trailing slash) must not read as 'not configured'."""
        result, _, _, mock_add = self._invoke(
            ["proj", "--projects-dir", f"{tmp_path}/"], tmp_path
        )

        assert result.exit_code == 0, result.output
        assert "No worktree opened" not in result.output
        mock_add.assert_called_once()


class TestCreateProjectIntegration:
    """`mael create-project` against real git repos, with only gh and the launch mocked."""

    def _run(self, tmp_path, repo_name="demo", url_name=None):
        """Seed a bare upstream, then run create-project end to end.

        ``url_name`` names the upstream repo when it differs from the requested
        name, so the test can tell which of the two the command actually used.
        """
        url_name = url_name or repo_name
        projects = tmp_path / "Projects"
        projects.mkdir()

        upstream = tmp_path / f"{url_name}.git"
        subprocess.run(
            ["git", "init", "--bare", "-b", "main", str(upstream)],
            check=True, capture_output=True,
        )
        seed = tmp_path / "seed"
        seed.mkdir()
        for filename, content in scaffold_files(repo_name).items():
            (seed / filename).write_text(content)
        for cmd in (
            ["git", "init", "-b", "main"],
            ["git", "add", "-A"],
            ["git", "-c", "user.email=t@t", "-c", "user.name=T",
             "commit", "-m", "seed"],
            ["git", "remote", "add", "origin", str(upstream)],
            ["git", "push", "-u", "origin", "main"],
        ):
            subprocess.run(cmd, cwd=seed, check=True, capture_output=True)

        launched = {}

        def fake_launch(path, project=None, worktree=None):
            launched.update(path=path, project=project, worktree=worktree)
            return True

        config = MagicMock(projects_dir=projects, open_command="code")
        with patch("maelstrom.cli.load_global_config", return_value=config), \
             patch("maelstrom.context.load_global_config", return_value=config), \
             patch("maelstrom.cli.create_project_repo", return_value=str(upstream)), \
             patch("maelstrom.cli.launch_claude_in_worktree", side_effect=fake_launch), \
             patch("maelstrom.cli.run_install_cmd"):
            result = CliRunner().invoke(cli, ["create-project", repo_name])
        return result, projects, launched

    def test_checks_out_the_project_and_opens_a_start_branch_worktree(self, tmp_path):
        result, projects, launched = self._run(tmp_path)

        assert result.exit_code == 0, result.output
        assert (projects / "demo" / ".mael").exists()

        # main lives in _main, so alpha is free and gets recycled for the
        # start branch rather than a fresh bravo being made.
        assert (projects / "demo" / "_main").exists()
        alpha = projects / "demo" / "demo-alpha"
        assert alpha.exists()
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=alpha, capture_output=True, text=True,
        ).stdout.strip()
        assert branch == "feat/start-project"
        assert launched["project"] == "demo"
        assert launched["worktree"] == "alpha"

    def test_the_worktree_carries_the_seed_and_generated_files(self, tmp_path):
        _, projects, _ = self._run(tmp_path)

        alpha = projects / "demo" / "demo-alpha"
        assert (alpha / "CLAUDE.md").exists()
        assert (alpha / ".gitignore").exists()
        assert (alpha / ".maelstrom.yaml").exists()
        # Generated per worktree, and ignored by the seeded .gitignore.
        assert (alpha / ".env").exists()
        assert (alpha / ".claude" / "CLAUDE.local.md").exists()

    def test_main_is_checked_out_beside_the_worktrees_not_into_one(self, tmp_path):
        """`_main` holds main, so no NATO worktree is burned on it."""
        _, projects, _ = self._run(tmp_path)

        main_dir = projects / "demo" / "_main"
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=main_dir, capture_output=True, text=True,
        ).stdout.strip()
        assert branch == "main"
        # A reference checkout, not a workspace: no ports, no .env.
        assert not (main_dir / ".env").exists()

    def test_reports_the_directory_that_was_actually_created(self, tmp_path):
        """`add_project` names the directory from the clone URL, not the argument.

        The two normally agree. When they don't, every path the command reports
        and hands on must follow the directory that exists, not the request.
        """
        result, projects, launched = self._run(
            tmp_path, repo_name="demo", url_name="other"
        )

        assert result.exit_code == 0, result.output
        assert (projects / "other").exists()
        assert str(projects / "other" / "other-alpha") in result.output
        assert launched["project"] == "other"
        assert (projects / "other" / "other-alpha").exists()


class TestMvProjectIntegration:
    """`mael mv-project` against a real git repo, with only global state mocked."""

    def _build(self, tmp_path, project_name="old"):
        """Build a bare-ish project with a nato worktree and a `_main` worktree."""
        projects = tmp_path / "Projects"
        projects.mkdir()
        project = projects / project_name
        project.mkdir()

        upstream = tmp_path / "up.git"
        subprocess.run(
            ["git", "init", "--bare", "-b", "main", str(upstream)],
            check=True, capture_output=True,
        )
        seed = tmp_path / "seed"
        seed.mkdir()
        (seed / "README.md").write_text("hi\n")
        for cmd in (
            ["git", "init", "-b", "main"],
            ["git", "add", "-A"],
            ["git", "-c", "user.email=t@t", "-c", "user.name=T", "commit", "-m", "seed"],
            ["git", "remote", "add", "origin", str(upstream)],
            ["git", "push", "-u", "origin", "main"],
        ):
            subprocess.run(cmd, cwd=seed, check=True, capture_output=True)

        subprocess.run(
            ["git", "clone", "--bare", str(upstream), str(project / ".git")],
            check=True, capture_output=True,
        )
        git_env = {**os.environ, "GIT_DIR": str(project / ".git")}
        subprocess.run(
            ["git", "config", "core.bare", "false"],
            cwd=project, env=git_env, check=True, capture_output=True,
        )
        # A bare clone has no remote-tracking refs; fetch them so the project
        # looks like one `mael add-project` made (and `mael doctor` accepts).
        for cmd in (
            ["git", "config", "remote.origin.fetch",
             "+refs/heads/*:refs/remotes/origin/*"],
            ["git", "fetch", "origin"],
        ):
            subprocess.run(cmd, cwd=project, check=True, capture_output=True)
        # Detach the repo's own HEAD so `_main` can hold `main`, which is the
        # layout `mael add-project` produces and `mael doctor` checks for.
        subprocess.run(
            ["git", "checkout", "--detach", "main"],
            cwd=project, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "worktree", "add", str(project / "_main"), "main"],
            cwd=project, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "worktree", "add", "-B", "feat/x",
             str(project / f"{project_name}-alpha"), "main"],
            cwd=project, check=True, capture_output=True,
        )
        (project / ".mael").touch()
        return projects, project

    def _run(self, tmp_path, projects, args, home=None):
        """Invoke mv-project with global state redirected into tmp_path."""
        home = home or (tmp_path / "home")
        home.mkdir(exist_ok=True)
        mael_dir = home / ".maelstrom"
        mael_dir.mkdir(exist_ok=True)
        config = MagicMock(projects_dir=projects, open_command="code")

        with patch("maelstrom.mv_project_cli.load_global_config", return_value=config), \
             patch("maelstrom.context.load_global_config", return_value=config), \
             patch("maelstrom.cli.load_global_config", return_value=config), \
             patch("maelstrom.context.get_maelstrom_dir", return_value=mael_dir), \
             patch("maelstrom.mv_project_cli.get_maelstrom_dir", return_value=mael_dir), \
             patch("maelstrom.task_store.get_maelstrom_dir", return_value=mael_dir), \
             patch("pathlib.Path.home", return_value=home), \
             patch("maelstrom.mv_project_cli.all_live_sessions", return_value=[]), \
             patch("maelstrom.mv_project_cli.update_claude_local_md"):
            return CliRunner().invoke(cli, ["mv-project"] + args)

    def test_git_works_inside_the_moved_worktree(self, tmp_path):
        """The `.git`-file direction: worktree -> admin dir."""
        projects, _ = self._build(tmp_path)
        result = self._run(tmp_path, projects, ["old", "new"])
        assert result.exit_code == 0, result.output

        status = subprocess.run(
            ["git", "status"], cwd=projects / "new" / "new-alpha",
            capture_output=True, text=True,
        )
        assert status.returncode == 0, status.stderr

    def test_worktree_list_shows_only_new_paths(self, tmp_path):
        """The `gitdir` direction: admin dir -> worktree."""
        projects, _ = self._build(tmp_path)
        self._run(tmp_path, projects, ["old", "new"])

        listing = subprocess.run(
            ["git", "worktree", "list"], cwd=projects / "new",
            capture_output=True, text=True, check=True,
        ).stdout
        assert str(projects / "new" / "new-alpha") in listing
        assert "old-alpha" not in listing

    def test_an_uncommitted_file_survives_the_rename(self, tmp_path):
        projects, project = self._build(tmp_path)
        (project / "old-alpha" / "wip.txt").write_text("unsaved work\n")

        self._run(tmp_path, projects, ["old", "new"])

        assert (projects / "new" / "new-alpha" / "wip.txt").read_text() == (
            "unsaved work\n"
        )

    def test_the_main_worktree_keeps_its_name_and_still_works(self, tmp_path):
        projects, _ = self._build(tmp_path)
        self._run(tmp_path, projects, ["old", "new"])

        main = projects / "new" / "_main"
        assert main.is_dir()
        status = subprocess.run(
            ["git", "status"], cwd=main, capture_output=True, text=True,
        )
        assert status.returncode == 0, status.stderr

    def test_a_task_moves_and_is_restamped(self, tmp_path):
        projects, _ = self._build(tmp_path)
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        tasks = home / ".maelstrom" / "tasks" / "old" / "todo"
        tasks.mkdir(parents=True)
        (tasks / "x.md").write_text(
            "---\nid: x\nproject: old\ntitle: A task\n---\n\nBody\n"
        )

        result = self._run(tmp_path, projects, ["old", "new"], home=home)
        assert result.exit_code == 0, result.output

        moved = home / ".maelstrom" / "tasks" / "new" / "todo" / "x.md"
        assert moved.exists()
        assert "project: new" in moved.read_text()
        assert not (home / ".maelstrom" / "tasks" / "old" / "todo" / "x.md").exists()

    def test_port_allocations_move_to_the_new_path_key(self, tmp_path):
        projects, project = self._build(tmp_path)
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        (home / ".maelstrom").mkdir(exist_ok=True)
        allocations = home / ".maelstrom" / "port_allocations.json"
        allocations.write_text(json.dumps({str(project): {"alpha": 310}}))

        self._run(tmp_path, projects, ["old", "new"], home=home)

        data = json.loads(allocations.read_text())
        assert data == {str(projects / "new"): {"alpha": 310}}

    def test_doctor_afterwards_prunes_nothing(self, tmp_path):
        """The whole point: doctor must not garbage-collect the port bases."""
        from maelstrom.doctor import run_doctor

        projects, project = self._build(tmp_path)
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        (home / ".maelstrom").mkdir(exist_ok=True)
        allocations = home / ".maelstrom" / "port_allocations.json"
        allocations.write_text(json.dumps({str(project): {"alpha": 310}}))

        self._run(tmp_path, projects, ["old", "new"], home=home)

        config = MagicMock(projects_dir=projects, open_command="code")
        with patch("maelstrom.context.get_maelstrom_dir",
                   return_value=home / ".maelstrom"), \
             patch("maelstrom.context.load_global_config", return_value=config), \
             patch("pathlib.Path.home", return_value=home):
            doctor_result = run_doctor(projects / "new")

        assert json.loads(allocations.read_text()) == {
            str(projects / "new"): {"alpha": 310}
        }
        bad = [
            c for c in doctor_result.checks
            if c.status.name in ("WARNING", "ERROR")
        ]
        assert not bad, [(c.status, c.message) for c in bad]
