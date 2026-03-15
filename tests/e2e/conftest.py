"""Shared fixtures for e2e tests."""

import json
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner

from maelstrom.context import GlobalConfig
from maelstrom.worktree import add_project, get_worktree_folder_name

from tests.git_helpers import create_commit, run_git, setup_git_repo


# --- Helpers ---


def wait_for(predicate, timeout=5.0, interval=0.1):
    """Poll predicate until truthy or raise TimeoutError."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    raise TimeoutError(f"Condition not met within {timeout}s")


def assert_process_dead(pid, timeout=5.0):
    """Assert a process is dead, reaping zombies as needed.

    After SIGKILL, child processes become zombies until reaped.
    This helper polls with os.waitpid to reap them.
    """
    from maelstrom.env import is_service_alive

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # Try to reap the zombie child
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        if not is_service_alive(pid):
            return
        time.sleep(0.1)
    raise AssertionError(f"Process {pid} still alive after {timeout}s")


def write_procfile(worktree_path, services):
    """Write a Procfile from a dict of {name: command}."""
    lines = [f"{name}: {cmd}" for name, cmd in services.items()]
    (worktree_path / "Procfile").write_text("\n".join(lines) + "\n")


# --- Dataclasses for fixture return values ---


@dataclass
class IsolatedMaelstrom:
    maelstrom_dir: Path
    projects_dir: Path


@dataclass
class TestProject:
    """A test project without git (for env tests)."""
    project_name: str
    project_path: Path
    worktree_name: str
    worktree_path: Path
    maelstrom_dir: Path
    projects_dir: Path


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


# --- Core isolation fixtures ---


@pytest.fixture
def isolated_maelstrom(tmp_path, monkeypatch):
    """Redirect ~/.maelstrom/ and ~/Projects/ to temp dirs (function-scoped)."""
    maelstrom_dir = tmp_path / ".maelstrom"
    maelstrom_dir.mkdir()
    projects_dir = tmp_path / "Projects"
    projects_dir.mkdir()

    # Patch get_maelstrom_dir in all modules that import it
    fake_get_dir = lambda: maelstrom_dir
    monkeypatch.setattr("maelstrom.context.get_maelstrom_dir", fake_get_dir)
    monkeypatch.setattr("maelstrom.env.get_maelstrom_dir", fake_get_dir)

    monkeypatch.setattr(
        "maelstrom.context.load_global_config",
        lambda: GlobalConfig(projects_dir=projects_dir),
    )

    return IsolatedMaelstrom(maelstrom_dir=maelstrom_dir, projects_dir=projects_dir)


@pytest.fixture(scope="module")
def isolated_maelstrom_module(tmp_path_factory):
    """Module-scoped isolation for tests sharing a git_project."""
    tmp_path = tmp_path_factory.mktemp("maelstrom")
    maelstrom_dir = tmp_path / ".maelstrom"
    maelstrom_dir.mkdir()
    projects_dir = tmp_path / "Projects"
    projects_dir.mkdir()

    mp = pytest.MonkeyPatch()
    fake_get_dir = lambda: maelstrom_dir
    mp.setattr("maelstrom.context.get_maelstrom_dir", fake_get_dir)
    mp.setattr("maelstrom.env.get_maelstrom_dir", fake_get_dir)
    mp.setattr(
        "maelstrom.context.load_global_config",
        lambda: GlobalConfig(projects_dir=projects_dir),
    )

    yield IsolatedMaelstrom(maelstrom_dir=maelstrom_dir, projects_dir=projects_dir)

    mp.undo()


# --- Non-git project fixture (for env tests) ---


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


# --- Git project fixtures ---


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
    (remote_path / ".maelstrom.yaml").write_text(
        "port_names:\n  - FRONTEND\n"
    )
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


# --- CLI runner ---


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
