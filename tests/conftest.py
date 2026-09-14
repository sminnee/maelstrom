"""Global test fixtures for maelstrom test suite."""

import os
import socket
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from maelstrom.cmux.client import RecordingCmuxClient
from maelstrom.cmux.model import CmuxLayout


def _can_bind_a_unix_socket() -> bool:
    """Whether this process may ``bind()`` at all.

    Probed rather than sniffed for, so it stays right whatever the sandbox is.
    """
    with tempfile.TemporaryDirectory() as tmp:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.bind(str(Path(tmp) / "probe.sock"))
        except OSError:
            return False
    return True


def pytest_collection_modifyitems(config, items):
    """Skip the tests that need a ``bind()`` when this process may not."""
    if _can_bind_a_unix_socket():
        return
    skip = pytest.mark.skip(reason="the sandbox denies bind() on a Unix socket")
    for item in items:
        if "binds_socket" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def collapsible_project():
    """A bare-clone project with a worktree on ``feature/work``.

    The source → bare-remote → working-clone pattern, so ``origin`` is a real
    remote and a rebase inside the command under test can fetch. Shared by the
    squash and uncommit suites, which drive the same two commands.

    Named apart from ``test_sync_flags``'s ``project_with_worktree``, which
    yields a third element and patches ``get_maelstrom_dir``. Two fixtures of
    one name, one shadowing the other by import, is how that suite's ``F811``
    suppressions arose.

    Yields ``(project_path, worktree_path)``.
    """
    import subprocess

    from tests.git_helpers import create_commit, run_git, setup_git_repo

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        source_path = tmp / "source"
        source_path.mkdir()
        setup_git_repo(source_path)
        create_commit(source_path, "README.md", "# Test\n", "Initial commit")
        run_git(source_path, "branch", "-M", "main")

        remote_path = tmp / "remote.git"
        subprocess.run(
            ["git", "clone", "--bare", str(source_path), str(remote_path)],
            check=True,
            capture_output=True,
        )

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

        head_sha = run_git(project_path, "rev-parse", "HEAD").stdout.strip()
        run_git(project_path, "update-ref", "--no-deref", "HEAD", head_sha)

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

        yield project_path, worktree_path


@pytest.fixture(autouse=True, scope="session")
def _block_real_cmux():
    """Prevent any test from accidentally invoking the real cmux binary.

    Patches the binary discovery used by the real transport to return None (no
    binary found) and removes CMUX_SOCKET_PATH from the environment, so
    ``current_client()`` returns None and nothing shells out.
    """
    saved = os.environ.pop("CMUX_SOCKET_PATH", None)
    with patch("maelstrom.cmux.client._find_cmux_cli", return_value=None):
        yield
    if saved is not None:
        os.environ["CMUX_SOCKET_PATH"] = saved


@pytest.fixture(autouse=True)
def _isolate_agent_paths(monkeypatch, tmp_path):
    """Keep every test off a real daemon root.

    Every command reads ``MAEL_AGENT_ROOT``, and a developer's shell sets it to
    a live root. An unpinned test would read the real spawn records and see
    whatever agents run on the machine.
    """
    monkeypatch.setenv("MAEL_AGENT_ROOT", str(tmp_path / "maelstrom"))


@pytest.fixture(autouse=True)
def _isolate_state_db_paths(monkeypatch, tmp_path):
    """Keep every test off the developer's live ``~/.maelstrom``.

    The desk ladder's import rung reads ``desk.json`` from there, so any test
    that migrates a database would otherwise pull the developer's real desk
    into its own. That read is silent: the rows arrive looking like the test's
    own, and the test fails somewhere else entirely.

    Autouse rather than per-test, because a test author cannot be expected to
    know that opening an in-memory database reaches a file at all.
    """
    monkeypatch.setattr(
        "maelstrom.state_db.paths.get_maelstrom_dir", lambda: tmp_path / "maelstrom"
    )


@pytest.fixture(autouse=True)
def _pin_harness_env(monkeypatch):
    """Keep the outer shell's harness out of the tests.

    ``resolve_harness`` detects the harness from ``CLAUDECODE`` /
    ``OPENCODE_TERMINAL``, so running pytest inside a Claude Code or OpenCode
    session would otherwise flip every default-launch test to that harness.
    Tests for the detection itself patch the env explicitly.
    """
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("OPENCODE_TERMINAL", raising=False)


@pytest.fixture(autouse=True)
def _block_real_claude_branch_gen(monkeypatch):
    """Prevent branch-name generation from shelling out to a live ``claude``.

    ``branch_name._run_claude`` invokes ``claude -p`` to pick a descriptive
    branch slug; in tests we force it to fail so generation falls back to the
    deterministic offline slug. Tests that want to exercise the model path
    inject a fake ``runner`` into ``generate_branch_name`` (or re-patch
    ``_run_claude`` themselves) — the later ``monkeypatch.setattr`` wins.
    """
    from maelstrom import branch_name

    def _unavailable(prompt: str) -> str:
        raise FileNotFoundError("claude")

    monkeypatch.setattr(branch_name, "_run_claude", _unavailable)


@pytest.fixture(autouse=True)
def _reset_task_db():
    """Forget the task CLI's cached database between tests.

    ``task_cli`` opens the state database once and keeps it in a module global,
    which is right in production — one process is one invocation. In the suite
    one process runs every test, so without this a test's temporary database
    would serve the next one, and the leak is invisible: the reads succeed and
    answer about the wrong notebook.
    """
    from maelstrom import task_cli

    task_cli._DB = None
    task_cli._CHECKED = False
    yield
    if task_cli._DB is not None:
        task_cli._DB.close()
    task_cli._DB = None
    task_cli._CHECKED = False


@pytest.fixture()
async def state_db():
    """A migrated in-memory state database, closed when the test ends.

    Four suites open one the same way. What each builds on top differs — a task
    table, a desk store, an export queue — so only the database is shared; the
    store fixtures stay with the contracts they exercise.
    """
    from maelstrom.state_db.migrate import open_state_db

    db = open_state_db(":memory:")
    await db.migrate()
    yield db
    db.close()


@pytest.fixture()
def store():
    """Shared task-table fixture for the model / CLI / actions test suites.

    Named ``store`` because that is what several hundred tests already call it;
    what it yields is an :class:`~maelstrom.task_table.InMemoryTaskTable`, the
    model's one injected collaborator now that the notebook is a table.

    The contract itself is exercised against both backends in
    ``tests/test_task_table.py``; here the in-memory twin keeps the behaviour
    suites fast and free of a database file.
    """
    from maelstrom.task_table import InMemoryTaskTable

    return InMemoryTaskTable()


@pytest.fixture()
def recording_layout():
    """Return a factory for a :class:`CmuxLayout` over a :class:`RecordingCmuxClient`.

    Call ``recording_layout(responses, name="ws")`` to build a layout whose
    client records every ``run`` call in ``client.calls`` and returns scripted
    results. ``responses`` is either a dict keyed by the exact args tuple or a
    callable ``fn(*args) -> str | None``. The returned tuple is
    ``(layout, client)`` so tests can assert on ``client.calls``.
    """

    def make(responses=None, name="myproject-alpha"):
        client = RecordingCmuxClient(responses)
        return CmuxLayout(client, name), client

    return make
