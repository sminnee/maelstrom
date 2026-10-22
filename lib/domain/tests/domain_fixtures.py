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

from mael_domain import worktree
from mael_domain.build_runs import BuildRuns
from mael_domain.cmux.api import FakeCmux
from mael_domain.cmux.mael_layout import MaelCmux


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
def _plain_terminal(monkeypatch):
    """Run every test as a person in a plain terminal.

    A developer who runs the suite from a cmux pane, or from a driven agent,
    has ``CMUX_WORKSPACE_ID`` or ``MAEL_HARNESS_TYPE`` set. Both change which
    transport a launch takes and whether a browser opens. A test that needs
    either sets it.
    """
    monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("MAEL_HARNESS_TYPE", raising=False)


@pytest.fixture
def fake_cmux(monkeypatch):
    """A running cmux, in memory, and a caller in one of its panes; see docs/dev/cmux.md."""
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "fake-workspace")
    cmux = FakeCmux()
    with patch.object(MaelCmux, "current", return_value=MaelCmux(cmux)):
        yield cmux


#: The callers ``MaelCmux.for_caller()`` tells apart. Only the first is in a pane.
IN_A_PANE, DRIVEN_AGENT, PLAIN_TERMINAL = "in-a-pane", "driven-agent", "plain-terminal"


@pytest.fixture(params=[IN_A_PANE, DRIVEN_AGENT, PLAIN_TERMINAL])
def caller(request, fake_cmux, monkeypatch):
    """Run the test once per caller of a running ``fake_cmux``; yields the caller's name."""
    if request.param == DRIVEN_AGENT:
        monkeypatch.setenv("MAEL_HARNESS_TYPE", "daemon")
    elif request.param == PLAIN_TERMINAL:
        monkeypatch.delenv("CMUX_WORKSPACE_ID")
    return request.param


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
def _always_fetch(monkeypatch):
    """Make every fetch reach the remote.

    Many tests fetch during setup, move origin, and then sync within seconds. A
    fresh freshness stamp would skip that sync's fetch, so the sync would not see
    the move. Tests of the stamp itself use ``stamp_honoured``.
    """
    monkeypatch.setattr(worktree, "FETCH_MAX_AGE", 0.0)


_PRODUCTION_FETCH_MAX_AGE = worktree.FETCH_MAX_AGE


@pytest.fixture
def stamp_honoured(monkeypatch):
    """Undo ``_always_fetch``: a fetch within the production window is skipped."""
    monkeypatch.setattr(worktree, "FETCH_MAX_AGE", _PRODUCTION_FETCH_MAX_AGE)


@pytest.fixture(autouse=True)
def _block_real_naming_model(monkeypatch):
    """Keep task naming off the network: every name is the deterministic slug.

    Tests that want the model path pass a fake ``generator`` (or re-patch
    ``default_generator`` themselves) — the later ``monkeypatch.setattr`` wins.
    """
    from mael_domain import task_metadata_generator

    monkeypatch.setattr(
        task_metadata_generator,
        "default_generator",
        task_metadata_generator.SlugGenerator,
    )


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
def migrated_notebook():
    """The isolated notebook root, holding a migrated ``state.db``.

    For a command that opens the database itself, and so refuses one that is
    behind this build. Returns the root.
    """
    import asyncio

    from mael_domain.state_db.migrate import migrate_state_db
    from mael_domain.state_db.paths import get_state_db_path

    path = get_state_db_path()
    asyncio.run(migrate_state_db(path))
    return path.parent


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


def build_project_with_worktree(tmp: Path) -> tuple[Path, Path, Path]:
    """Build a bare-clone project ``test-repo`` under ``tmp``.

    The source → bare-remote → working-clone pattern, so ``origin`` is a real
    remote. The project-root HEAD is detached, and the worktree
    ``test-repo-alpha`` is on ``feature/work`` from ``origin/main``.

    Returns ``(project_path, worktree_path, remote_path)``.
    """
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
    return project_path, worktree_path, remote_path


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
        project_path, worktree_path, remote_path = build_project_with_worktree(tmp)

        maelstrom_dir = tmp / "maelstrom-home"
        maelstrom_dir.mkdir()
        # The legacy ``~/.maelstrom.yaml`` is read from the home directory, so
        # a developer's own ``dev_host:`` would otherwise reach the rows.
        with (
            patch("mael_domain.context.get_maelstrom_dir", return_value=maelstrom_dir),
            patch("pathlib.Path.home", return_value=tmp / "home"),
        ):
            yield project_path, worktree_path, remote_path


@pytest.fixture
def collapsible_project():
    """A bare-clone project with a worktree on ``feature/work``.

    Shared by the squash and uncommit suites, which drive the same two
    functions. Named apart from ``project_with_worktree``, which yields a third
    element and patches ``get_maelstrom_dir``.

    Yields ``(project_path, worktree_path)``.
    """
    with TemporaryDirectory() as tmpdir:
        project_path, worktree_path, _ = build_project_with_worktree(Path(tmpdir))
        yield project_path, worktree_path


@pytest.fixture
def quiet_finalize():
    """Stub the finalize side effects, so a worktree stays clean for git asserts."""
    with (
        patch("mael_domain.worktree.update_claude_local_md", return_value=False),
        patch("mael_domain.worktree.run_install_cmd"),
        patch("mael_domain.worktree.setup_claude_memory_symlink"),
    ):
        yield


_FAKE_TAILSCALE = """#!/bin/sh
echo "$@" >> "$ARGV_LOG"
if [ -n "$REFUSE" ]; then
  echo "your Tailscale account does not support getting TLS certs" >&2
  exit 1
fi
while [ $# -gt 0 ]; do
  case "$1" in
    --cert-file) echo CERT > "$2"; shift ;;
    --key-file) echo KEY > "$2"; chmod 644 "$2"; shift ;;
  esac
  shift
done
"""


@pytest.fixture
def fake_tailscale(tmp_path, monkeypatch) -> Path:
    """A stand-in ``tailscale`` on ``PATH``; returns its argv log.

    It writes the files the way the real one does, with a world-readable key.
    Set ``REFUSE`` in the environment to make it refuse, as an account without
    HTTPS certificates does.
    """
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    exe = bin_dir / "tailscale"
    exe.write_text(_FAKE_TAILSCALE)
    exe.chmod(0o755)
    log = tmp_path / "tailscale-argv"
    monkeypatch.setenv("PATH", f"{bin_dir}:/bin:/usr/bin")
    monkeypatch.setenv("ARGV_LOG", str(log))
    return log


class FakeBuildRuns(BuildRuns):
    """Completed runs from a fixed answer; ``None`` is a failed read.

    ``reads`` records each ``(project, workflow)`` asked about.
    """

    def __init__(self, answer):
        self.answer = answer
        self.reads = []

    async def completed(self, project, trigger):
        self.reads.append((project, trigger.workflow))
        return self.answer
