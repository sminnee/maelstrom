"""Fixtures for the domain's git and worktree workflows."""

from dataclasses import dataclass
from pathlib import Path

import pytest
from e2e_fixtures import (  # noqa: F401  (pytest fixtures, found by name)
    isolated_maelstrom_fixture,
    isolated_maelstrom_module_fixture,
)
from git_helpers import create_commit, run_git, setup_git_repo

from mael_domain.worktree import add_project
from mael_domain.worktree_model import get_worktree_folder_name


@dataclass
class GitProject:
    """A test project with real git repo (for worktree tests)."""

    project_name: str
    project_path: Path
    remote_path: Path
    worktree_name: str
    worktree_path: Path
    maelstrom_dir: Path
    projects_dir: Path


@pytest.fixture
def git_project(isolated_maelstrom):
    """Create a project using add_project() against a local source repo (function-scoped)."""
    return _create_git_project(isolated_maelstrom)


@pytest.fixture(scope="module")
def git_project_module(isolated_maelstrom_module):
    """Module-scoped git project for workflow tests sharing a single repo."""
    return _create_git_project(isolated_maelstrom_module)


def _create_git_project(isolated):
    """Shared implementation for creating a git project fixture."""
    base = isolated.projects_dir.parent
    projects_dir = isolated.projects_dir

    # 1. Create a local source repo (acts as the "remote")
    remote_path = base / "testproj-origin"
    remote_path.mkdir()
    setup_git_repo(remote_path)
    (remote_path / ".maelstrom.yaml").write_text("port_names:\n  - FRONTEND\n")
    run_git(remote_path, "add", ".")
    create_commit(remote_path, "README.md", "# Test Project\n", "Initial commit")
    run_git(remote_path, "branch", "-M", "main")
    # Allow pushes to checked-out branch (needed because this is a local repo, not bare)
    run_git(remote_path, "config", "receive.denyCurrentBranch", "ignore")

    # 2. Use add_project to set up the maelstrom project structure
    project_path = add_project(str(remote_path), projects_dir=projects_dir)
    project_name = project_path.name

    # 3. Point origin at the source repo (add_project may set it to the path already)
    run_git(project_path, "config", "remote.origin.url", str(remote_path))

    # 4. Configure git user in the project and alpha worktree
    run_git(project_path, "config", "user.email", "test@test.com")
    run_git(project_path, "config", "user.name", "Test")

    worktree_name = "alpha"
    folder_name = get_worktree_folder_name(project_name, worktree_name)
    worktree_path = project_path / folder_name

    run_git(worktree_path, "config", "user.email", "test@test.com")
    run_git(worktree_path, "config", "user.name", "Test")

    return GitProject(
        project_name=project_name,
        project_path=project_path,
        remote_path=remote_path,
        worktree_name=worktree_name,
        worktree_path=worktree_path,
        maelstrom_dir=isolated.maelstrom_dir,
        projects_dir=isolated.projects_dir,
    )
