"""Tests for `mael project list` and the model function behind it."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from maelstrom.cli import cli
from maelstrom.worktree import ProjectInfo, list_projects
from tests.git_helpers import create_commit, run_git, setup_git_repo


def _make_project(projects_dir: Path, name: str, worktrees: list[str]) -> Path:
    """A real maelstrom-aware project: a `.mael` marker and a git repo with worktrees."""
    project = projects_dir / name
    project.mkdir(parents=True)
    (project / ".mael").touch()
    setup_git_repo(project)
    create_commit(project, "README.md", "hello", "initial")

    for worktree_name in worktrees:
        run_git(project, "worktree", "add", "-b", worktree_name, str(project / f"{name}-{worktree_name}"))

    return project


class TestListProjects:
    """The model function: one entry per project, with its worktree count."""

    def test_counts_worktrees_and_excludes_the_project_root(self, tmp_path):
        """Discovery, ordering and the root exclusion, against real repos."""
        projects_dir = tmp_path / "Projects"
        _make_project(projects_dir, "alpha-project", ["alpha", "bravo"])
        _make_project(projects_dir, "bravo-project", [])
        # A directory with no .mael marker is not a project.
        (projects_dir / "not-a-project").mkdir()

        result = list_projects(projects_dir)

        assert [(p.name, p.worktree_count) for p in result] == [
            ("alpha-project", 2),
            ("bravo-project", 0),
        ]
        assert result[0].path == projects_dir / "alpha-project"

    def test_a_missing_projects_dir_gives_no_projects(self, tmp_path):
        assert list_projects(tmp_path / "not-there") == []

    def test_a_project_whose_git_call_fails_counts_zero(self, tmp_path):
        """A marked directory that is not a git repo counts 0 rather than raising."""
        projects_dir = tmp_path / "Projects"
        broken = projects_dir / "broken-project"
        broken.mkdir(parents=True)
        (broken / ".mael").touch()

        result = list_projects(projects_dir)

        assert len(result) == 1
        assert result[0].name == "broken-project"
        assert result[0].worktree_count == 0


class TestProjectListCommand:
    """The CLI boundary: ``mael project list``."""

    def _invoke(self, projects, args, home=Path("/Users/example")):
        """Run the command with the project scan and $HOME both faked."""
        runner = CliRunner()
        with patch("maelstrom.project_cli.load_global_config") as mock_config:
            mock_config.return_value = MagicMock(projects_dir=home / "Projects")
            with patch("maelstrom.project_cli.list_projects", return_value=projects):
                with patch("maelstrom.util.Path.home", return_value=home):
                    return runner.invoke(cli, args)

    def test_the_table_lists_every_project_with_home_abbreviated(self):
        home = Path("/Users/example")
        projects = [
            ProjectInfo(name="alpha-project", path=home / "Projects/alpha-project", worktree_count=2),
            ProjectInfo(name="bravo-project", path=home / "Projects/bravo-project", worktree_count=0),
        ]

        result = self._invoke(projects, ["project", "list"], home=home)

        assert result.exit_code == 0
        assert "PROJECT" in result.output
        assert "WORKTREES" in result.output
        assert "~/Projects/alpha-project" in result.output
        assert "~/Projects/bravo-project" in result.output
        assert str(home) not in result.output

    def test_no_projects_says_so(self):
        result = self._invoke([], ["project", "list"])

        assert result.exit_code == 0
        assert result.output.strip() == "No projects found."

    def test_json_lists_every_project(self):
        home = Path("/Users/example")
        projects = [
            ProjectInfo(name="alpha-project", path=home / "Projects/alpha-project", worktree_count=2),
        ]

        result = self._invoke(projects, ["--json", "project", "list"], home=home)

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == {
            "projects": [
                {
                    "name": "alpha-project",
                    "path": str(home / "Projects/alpha-project"),
                    "worktree_count": 2,
                }
            ]
        }

    def test_json_with_no_projects_is_an_empty_list(self):
        result = self._invoke([], ["--json", "project", "list"])

        assert result.exit_code == 0
        assert json.loads(result.output) == {"projects": []}

    def test_end_to_end_over_a_real_projects_directory(self, tmp_path):
        """The CLI reaches the real scan. Only the config is faked."""
        projects_dir = tmp_path / "Projects"
        _make_project(projects_dir, "alpha-project", ["alpha"])

        runner = CliRunner()
        with patch("maelstrom.project_cli.load_global_config") as mock_config:
            mock_config.return_value = MagicMock(projects_dir=projects_dir)
            result = runner.invoke(cli, ["--json", "project", "list"])

        assert result.exit_code == 0
        assert json.loads(result.output) == {
            "projects": [
                {
                    "name": "alpha-project",
                    "path": str(projects_dir / "alpha-project"),
                    "worktree_count": 1,
                }
            ]
        }
