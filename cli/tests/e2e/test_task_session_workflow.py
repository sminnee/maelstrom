"""One launch of one task, end to end: the Agent record links them.

Nothing here is faked but ``claude`` itself. A real agent daemon runs on a
private daemon root, ``mael task run`` starts a real driven agent through it,
and the process table is read for real. So this is the one test that proves the
whole chain — task, Agent record, session — rather than each reader's half.
"""

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from click.testing import CliRunner
from git_helpers import create_commit, run_git, setup_git_repo

from mael_agent.agent_transport import ROOT_ENV, client, daemon_paths
from mael_agent.harness_model import HARNESS_TYPE_ENV, TRANSPORT_DAEMON
from mael_cli.cli import cli
from mael_domain import session_discovery
from mael_domain.agent_store import SqliteAgentStore
from mael_domain.state_db.migrate import open_state_db
from mael_domain.worktree import add_project

#: A stand-in ``claude``. It keeps the argv the daemon gave it and reads its
#: stdin until the daemon closes it. ``exec -a`` names the process ``claude``,
#: which is what the live-session sweep reads off the process table. The loop
#: is a shell builtin on purpose: a Python child re-executes itself on macOS
#: and takes its own name back, and ``bash -c`` with one command becomes that
#: command.
FAKE_CLAUDE = """#!/bin/bash
exec -a claude /bin/bash -c 'while read -r line; do :; done' claude "$@"
"""


@pytest.fixture
def project(isolated_maelstrom) -> str:
    """A real git project under the isolated projects directory. Its name."""
    remote = isolated_maelstrom.projects_dir.parent / "testproj-origin"
    remote.mkdir()
    setup_git_repo(remote)
    (remote / ".maelstrom.yaml").write_text("port_names: []\n")
    run_git(remote, "add", ".")
    create_commit(remote, "README.md", "# Test Project\n", "Initial commit")
    run_git(remote, "branch", "-M", "main")
    run_git(remote, "config", "receive.denyCurrentBranch", "ignore")
    path = add_project(str(remote), projects_dir=isolated_maelstrom.projects_dir)
    run_git(path, "config", "remote.origin.url", str(remote))
    run_git(path, "config", "user.email", "test@test.com")
    run_git(path, "config", "user.name", "Test")
    return path.name


@pytest.fixture
def daemon_root(monkeypatch, tmp_path):
    """A running agent daemon on a root of its own, with the stand-in ``claude``.

    Short and under ``/tmp``: a Unix socket path is capped near 104 bytes, and
    pytest's ``tmp_path`` on macOS is longer than that.
    """
    root = Path(tempfile.mkdtemp(prefix="mad-", dir="/tmp"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text(FAKE_CLAUDE)
    claude.chmod(0o755)
    monkeypatch.setenv(ROOT_ENV, str(root))
    log = root / "daemon.log"
    with log.open("w") as out:
        daemon = subprocess.Popen(
            [str(Path(sys.executable).with_name("mael-agent-daemon")), "serve"],
            env={
                **os.environ,
                ROOT_ENV: str(root),
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            },
            stdout=out,
            stderr=subprocess.STDOUT,
        )
    try:
        socket = daemon_paths(root).socket
        deadline = time.monotonic() + 15
        reply: dict = {}
        while "daemon" not in reply:
            assert daemon.poll() is None, log.read_text()
            assert time.monotonic() < deadline, f"no answer: {reply}"
            if socket.exists():
                reply = asyncio.run(
                    client(socket_path=str(socket)).request({"cmd": "ping"})
                )
            time.sleep(0.05)
        yield root
    finally:
        # The daemon's own shutdown stops every child it holds.
        daemon.send_signal(signal.SIGTERM)
        try:
            daemon.wait(timeout=10)
        except subprocess.TimeoutExpired:
            daemon.kill()
            daemon.wait()
        shutil.rmtree(root, ignore_errors=True)


def mael(*args: str):
    """Run one ``mael`` command in this process, as a fresh invocation."""
    return CliRunner().invoke(cli, list(args))


@pytest.mark.slow
@pytest.mark.binds_socket
@pytest.mark.usefixtures("daemon_root")
def test_a_launched_task_is_linked_to_its_session(
    isolated_maelstrom, project, monkeypatch
):
    # A driven agent's launch places no pane, so the launch needs no cmux.
    monkeypatch.setenv(HARNESS_TYPE_ENV, TRANSPORT_DAEMON)
    # This suite may itself run inside a task's session.
    monkeypatch.delenv("MAEL_TASK_ID", raising=False)
    monkeypatch.delenv("MAEL_TASK_PARENT", raising=False)

    assert mael("admin", "migrate").exit_code == 0
    added = mael("task", "add", "Ship it", "--project", project)
    assert added.exit_code == 0, added.output
    task_id = added.output.strip()

    run = mael("task", "run", task_id, "--project", project, "--daemon")
    assert run.exit_code == 0, run.output

    async def records() -> list[dict]:
        db = open_state_db(isolated_maelstrom.maelstrom_dir / "state.db")
        try:
            return await SqliteAgentStore(db).for_task(f"{project}/{task_id}")
        finally:
            db.close()

    # One record on the task, written by the launch.
    [record] = asyncio.run(records())
    session_id = record["session_id"]
    assert session_id

    # One live session, on the session id the record names. Other sessions on
    # the machine are not this test's, so the read is narrowed to its project.
    def in_project(sess: session_discovery.LiveSession) -> bool:
        return isolated_maelstrom.projects_dir.resolve() in sess.cwd.resolve().parents

    deadline = time.monotonic() + 10
    live: list[session_discovery.LiveSession] = []
    while not live and time.monotonic() < deadline:
        swept = asyncio.run(session_discovery.all_live_sessions())
        live = [sess for sess in swept if in_project(sess)]
        time.sleep(0.1)
    assert [sess.session_id for sess in live] == [session_id]

    # The session id alone names the task.
    info = mael("--json", "session", "info", session_id)
    assert info.exit_code == 0, info.output
    assert json.loads(info.output)["task"] == task_id

    # And the record is what blocks a second launch of the task.
    again = mael("task", "run", task_id, "--project", project, "--daemon")
    assert again.exit_code != 0
    assert "already has a live Claude session" in again.output
    assert len(asyncio.run(records())) == 1
