"""Tests for maelstrom.cli module."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from click.testing import CliRunner

from maelstrom.cli import cli
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
