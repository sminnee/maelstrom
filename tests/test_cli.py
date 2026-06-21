"""Tests for maelstrom.cli module."""

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

from click.testing import CliRunner

from maelstrom.cli import cli
from maelstrom.worktree import WorktreeInfo
from maelstrom.worktree_model import CopyBackResult


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
                    with patch("maelstrom.cli.is_worktree_closed", return_value=False):
                        with patch("maelstrom.cli.get_worktree_dirty_files", return_value=["file.txt"]):
                            with patch("maelstrom.cli.get_local_only_commits", return_value=2):
                                with patch("maelstrom.cli.get_pr_number_and_commits", return_value=(42, 5)):
                                    with patch("maelstrom.cli.get_active_ide_sessions", return_value={wt_path: 1}):
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
        assert wt["ide_active"] is True

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
                    with patch("maelstrom.cli.is_worktree_closed", return_value=True):
                        with patch("maelstrom.cli.get_worktree_dirty_files", return_value=[]):
                            with patch("maelstrom.cli.get_active_ide_sessions", return_value={}):
                                result = runner.invoke(cli, ["--json", "list-all"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        wt = data["projects"][0]["worktrees"][0]
        assert wt["is_closed"] is True
        assert wt["branch"] is None
        assert wt["dirty_files"] == 0
        assert wt["local_commits"] == 0

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

            mock_stop.assert_called_once_with("myproject", "alpha")
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

            mock_stop.assert_called_once_with("myproject", "alpha")
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
        stack.enter_context(patch("maelstrom.worktree._setup_claude_memory_symlink"))
        stack.enter_context(patch("maelstrom.worktree.update_claude_local_md", return_value=False))
        stack.enter_context(patch("maelstrom.worktree.run_install_cmd"))
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
                "proj", "bravo", project_path, worktree_path,
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
                "maelstrom.cli._ensure_cmux_browser",
            ))
            print_status = stack.enter_context(patch(
                "maelstrom.cli._print_service_status",
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
