"""Tests for maelstrom.cli module."""

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

from click.testing import CliRunner

from maelstrom.cli import cli, _compute_app_build_hash, _should_rebuild_app, _store_build_hash
from maelstrom.worktree import WorktreeInfo


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


class TestBuildHash:
    """Tests for build hash computation and skip-rebuild logic."""

    def test_compute_app_build_hash(self):
        """Test that _compute_app_build_hash returns a hex digest."""
        mock_result = MagicMock()
        mock_result.stdout = "100644 blob abc123\tapp/main.ts\n"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            h = _compute_app_build_hash(Path("/fake/repo"))

        assert len(h) == 64  # SHA-256 hex digest
        mock_run.assert_called_once()
        args = mock_run.call_args
        assert "git" in args[0][0]
        assert "ls-tree" in args[0][0]

    def test_should_rebuild_no_hash_file(self, tmp_path):
        """Test that _should_rebuild_app returns True when no hash file exists."""
        repo = tmp_path / "repo"
        app_target = repo / "app" / "src-tauri" / "target"
        app_target.mkdir(parents=True)

        with patch("maelstrom.cli._compute_app_build_hash", return_value="abc123"):
            should_rebuild, current_hash = _should_rebuild_app(repo)

        assert should_rebuild is True
        assert current_hash == "abc123"

    def test_should_rebuild_hash_matches(self, tmp_path):
        """Test that _should_rebuild_app returns False when hash matches."""
        repo = tmp_path / "repo"
        app_target = repo / "app" / "src-tauri" / "target"
        app_target.mkdir(parents=True)
        (app_target / ".build-hash").write_text("abc123\n")

        with patch("maelstrom.cli._compute_app_build_hash", return_value="abc123"):
            should_rebuild, current_hash = _should_rebuild_app(repo)

        assert should_rebuild is False
        assert current_hash == "abc123"

    def test_should_rebuild_hash_differs(self, tmp_path):
        """Test that _should_rebuild_app returns True when hash differs."""
        repo = tmp_path / "repo"
        app_target = repo / "app" / "src-tauri" / "target"
        app_target.mkdir(parents=True)
        (app_target / ".build-hash").write_text("old_hash\n")

        with patch("maelstrom.cli._compute_app_build_hash", return_value="new_hash"):
            should_rebuild, current_hash = _should_rebuild_app(repo)

        assert should_rebuild is True
        assert current_hash == "new_hash"

    def test_store_build_hash(self, tmp_path):
        """Test that _store_build_hash writes the hash file."""
        repo = tmp_path / "repo"
        app_target = repo / "app" / "src-tauri" / "target"
        app_target.mkdir(parents=True)

        _store_build_hash(repo, "my_hash_value")

        hash_file = app_target / ".build-hash"
        assert hash_file.exists()
        assert hash_file.read_text().strip() == "my_hash_value"

    def test_store_build_hash_creates_directory(self, tmp_path):
        """Test that _store_build_hash creates target dir if missing."""
        repo = tmp_path / "repo"

        _store_build_hash(repo, "my_hash")

        hash_file = repo / "app" / "src-tauri" / "target" / ".build-hash"
        assert hash_file.exists()


class TestRemoveMultiTarget:
    """Tests for multi-target remove command."""

    def test_rm_multiple_worktrees(self):
        """Test that mael rm accepts multiple worktree arguments."""
        runner = CliRunner()
        project_path = Path("/tmp/claude/projects/myproject")

        with patch("maelstrom.cli.resolve_context") as mock_resolve:
            with patch("maelstrom.cli.get_worktree_dirty_files", return_value=[]):
                with patch("maelstrom.cli.remove_worktree_by_path") as mock_remove:
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
                    with patch.object(Path, "exists", return_value=True):
                        result = runner.invoke(cli, ["rm", "bad", "bravo"])

            # Should exit with error (one target failed)
            assert result.exit_code == 1
            assert "bad target" in result.output


class TestCloseMultiTarget:
    """Tests for multi-target close command."""

    def test_close_no_args_uses_cwd(self):
        """Test that mael close with no args still uses cwd detection."""
        runner = CliRunner()

        with patch("maelstrom.cli.resolve_context") as mock_resolve:
            mock_ctx = MagicMock()
            mock_ctx.worktree = "alpha"
            mock_ctx.worktree_path = MagicMock()
            mock_ctx.worktree_path.exists.return_value = True
            mock_resolve.return_value = mock_ctx

            with patch("maelstrom.cli.close_worktree") as mock_close:
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
                ctx.worktree_path = MagicMock()
                ctx.worktree_path.exists.return_value = True
                return ctx

            mock_resolve.side_effect = [make_ctx("alpha"), make_ctx("bravo")]

            with patch("maelstrom.cli.close_worktree") as mock_close:
                mock_close.return_value = MagicMock(success=True, message="Closed")
                result = runner.invoke(cli, ["close", "alpha", "bravo"])

            assert mock_close.call_count == 2
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
