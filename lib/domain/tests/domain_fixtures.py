"""Fixtures the domain suites share with the CLI's suite.

``lib/domain/tests/conftest.py`` and ``cli/tests/conftest.py`` both import them by
name. ``pythonpath`` in ``pyproject.toml`` puts this directory on the path, so
either suite can import it whichever one pytest collects first.
"""

import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from git_helpers import create_commit, run_git, setup_git_repo


@pytest.fixture(autouse=True, scope="session")
def _block_real_cmux():
    """Prevent any test from accidentally invoking the real cmux binary.

    Patches the binary discovery used by the real transport to return None (no
    binary found) and removes CMUX_SOCKET_PATH from the environment, so
    ``current_client()`` returns None and nothing shells out.
    """
    saved = os.environ.pop("CMUX_SOCKET_PATH", None)
    with patch("mael_domain.cmux.client._find_cmux_cli", return_value=None):
        yield
    if saved is not None:
        os.environ["CMUX_SOCKET_PATH"] = saved


@pytest.fixture(autouse=True)
def _isolate_notebook_root(monkeypatch, tmp_path):
    """Keep every test off the developer's real task notebook.

    Every notebook path — ``state.db``, ``desk.json`` and the task export —
    hangs off ``MAEL_NOTEBOOK_ROOT``, and a developer's shell sets it to a live
    root. An unpinned test would read *and write* the real notebook.

    Replaces a fixture that patched ``get_maelstrom_dir`` on
    ``state_db.paths``. That module reads the root rather than the home
    directory now, so one pinned variable covers what the patch did and the
    store's own ``tasks_root`` besides — which the patch never reached.

    Autouse and set rather than deleted: the root has no fallback, so an absent
    one would make every task test fail on the refusal instead of exercising
    what it means to test. Tests for the refusal delete it explicitly.
    """
    monkeypatch.setenv("MAEL_NOTEBOOK_ROOT", str(tmp_path / "maelstrom"))


@pytest.fixture(autouse=True)
def _mark_test_commands_production(monkeypatch):
    """Keep command-output tests free of the non-production warning."""
    monkeypatch.setenv("MAEL_PRODUCTION", "1")


@pytest.fixture(autouse=True)
def _block_real_claude_branch_gen(monkeypatch):
    """Prevent branch-name generation from shelling out to a live ``claude``.

    ``branch_name._run_claude`` invokes ``claude -p`` to pick a descriptive
    branch slug; in tests we force it to fail so generation falls back to the
    deterministic offline slug. Tests that want to exercise the model path
    inject a fake ``runner`` into ``generate_branch_name`` (or re-patch
    ``_run_claude`` themselves) — the later ``monkeypatch.setattr`` wins.
    """
    from mael_domain import branch_name

    def _unavailable(prompt: str) -> str:
        raise FileNotFoundError("claude")

    monkeypatch.setattr(branch_name, "_run_claude", _unavailable)


@pytest.fixture()
async def state_db():
    """A migrated in-memory state database, closed when the test ends.

    Four suites open one the same way. What each builds on top differs — a task
    table, a desk store, an export queue — so only the database is shared; the
    store fixtures stay with the contracts they exercise.
    """
    from mael_domain.state_db.migrate import open_state_db

    db = open_state_db(":memory:")
    await db.migrate()
    yield db
    db.close()


@pytest.fixture()
def store():
    """Shared task-table fixture for the model / CLI / actions test suites.

    Named ``store`` because that is what several hundred tests already call it;
    what it yields is an :class:`~mael_domain.task_table.InMemoryTaskTable`, the
    model's one injected collaborator now that the notebook is a table.

    The contract itself is exercised against both backends in
    ``lib/domain/tests/test_task_table.py``; here the in-memory twin keeps the behaviour
    suites fast and free of a database file.
    """
    from mael_domain.task_table import InMemoryTaskTable

    return InMemoryTaskTable()


@pytest.fixture
def project_with_worktree():
    """A bare-clone project ``test-repo`` with a worktree ``test-repo-alpha``.

    Mirrors maelstrom's real layout so port-allocation name extraction works:
    the worktree folder is ``<project>-<nato>``. ``get_maelstrom_dir`` is patched
    to a temp directory so port allocations don't touch the real home dir.

    Yields ``(project_path, worktree_path, remote_path)``.
    """
    with TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Source repo with an initial commit on main.
        source_path = tmp / "source"
        source_path.mkdir()
        setup_git_repo(source_path)
        create_commit(source_path, "README.md", "# Test\n", "Initial commit")
        run_git(source_path, "branch", "-M", "main")

        # Bare "remote".
        remote_path = tmp / "remote.git"
        subprocess.run(
            ["git", "clone", "--bare", str(source_path), str(remote_path)],
            check=True,
            capture_output=True,
        )

        # Project root: bare clone in .git (maelstrom layout).
        project_path = tmp / "test-repo"
        project_path.mkdir()
        git_dir = project_path / ".git"
        subprocess.run(
            ["git", "clone", "--bare", str(remote_path), str(git_dir)],
            check=True,
            capture_output=True,
        )
        run_git(project_path, "config", "core.bare", "true")
        run_git(
            project_path,
            "config",
            "remote.origin.fetch",
            "+refs/heads/*:refs/remotes/origin/*",
        )
        run_git(project_path, "config", "user.email", "test@test.com")
        run_git(project_path, "config", "user.name", "Test")
        run_git(project_path, "fetch", "origin")

        # Detach project-root HEAD so main isn't checked out there.
        head_sha = run_git(project_path, "rev-parse", "HEAD").stdout.strip()
        run_git(project_path, "update-ref", "--no-deref", "HEAD", head_sha)

        # Worktree on a feature branch, folder named <project>-alpha.
        worktree_path = project_path / "test-repo-alpha"
        subprocess.run(
            [
                "git",
                "worktree",
                "add",
                "-b",
                "feature/work",
                str(worktree_path),
                "origin/main",
            ],
            cwd=project_path,
            check=True,
            capture_output=True,
        )
        run_git(worktree_path, "config", "user.email", "test@test.com")
        run_git(worktree_path, "config", "user.name", "Test")

        maelstrom_dir = tmp / "maelstrom-home"
        maelstrom_dir.mkdir()
        with patch("mael_domain.context.get_maelstrom_dir", return_value=maelstrom_dir):
            yield project_path, worktree_path, remote_path
