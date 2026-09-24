"""Fixtures for the CLI's env workflow."""

import json
import os
import signal
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner
from e2e_fixtures import isolated_maelstrom_fixture  # noqa: F401  (a pytest fixture)
from env_workflow_support import write_procfile


@dataclass
class TestProject:
    """A test project without git (for env tests)."""

    project_name: str
    project_path: Path
    worktree_name: str
    worktree_path: Path
    maelstrom_dir: Path
    projects_dir: Path


@pytest.fixture
def test_project(isolated_maelstrom):
    """Create a minimal project directory with Procfile (no git)."""
    project_name = "testproj"
    project_path = isolated_maelstrom.projects_dir / project_name
    project_path.mkdir()

    worktree_name = "alpha"
    folder_name = f"{project_name}-{worktree_name}"
    worktree_path = project_path / folder_name
    worktree_path.mkdir()

    # Write a simple Procfile
    write_procfile(worktree_path, {"web": "sleep 3600"})

    # Write .maelstrom.yaml with no install_cmd
    (worktree_path / ".maelstrom.yaml").write_text("port_names: []\n")

    # Write a minimal .env
    (worktree_path / ".env").write_text("WORKTREE=alpha\n")

    return TestProject(
        project_name=project_name,
        project_path=project_path,
        worktree_name=worktree_name,
        worktree_path=worktree_path,
        maelstrom_dir=isolated_maelstrom.maelstrom_dir,
        projects_dir=isolated_maelstrom.projects_dir,
    )


@pytest.fixture
def second_worktree(test_project):
    """Add a bravo worktree directory to the test project."""
    worktree_name = "bravo"
    folder_name = f"{test_project.project_name}-{worktree_name}"
    worktree_path = test_project.project_path / folder_name
    worktree_path.mkdir()

    write_procfile(worktree_path, {"web": "sleep 3600"})
    (worktree_path / ".maelstrom.yaml").write_text("port_names: []\n")
    (worktree_path / ".env").write_text("WORKTREE=bravo\n")

    return worktree_path


@pytest.fixture
def cli_runner():
    return CliRunner()


# --- Process cleanup (for env tests) ---


@pytest.fixture(autouse=False)
def process_cleanup(isolated_maelstrom):
    """Kill any leftover processes after env tests."""
    yield
    envs_dir = isolated_maelstrom.maelstrom_dir / "envs"
    if not envs_dir.exists():
        return
    for state_file in envs_dir.rglob("*.json"):
        try:
            data = json.loads(state_file.read_text())
            services = data.get("services", [])
            for svc in services:
                pid = svc.get("pid")
                if pid:
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
        except (json.JSONDecodeError, OSError):
            pass
