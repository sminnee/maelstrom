"""Task session logs on the server: the exit archive, the sweep, the push, and the routes.

The archive's own rules are in ``lib/domain/tests/test_session_logs.py``. These
tests prove only that the server calls it at the right moments, with its roots
injected, and that a delegated agent's log reaches its **Task home**.
"""

import asyncio
from pathlib import Path

import pytest
from test_orchestrator_server import (
    NOW,
    Harness,
    _count_calls,
    agent_row,
    cobalt_and_parent,
    has_task,
    merged,
    operated,
    run,
    session_of,
    until,
)

from mael_domain import task as model
from mael_domain.auth.model import DELEGATE, ApiKey, hash_secret
from mael_domain.session_logs import SessionLogArchive, SessionRef
from mael_orchestrator.sources import RemoteTaskSource, TaskSourceRemote

TASK = "northwind/NORT-7"
DELEGATE_KEY = "mael_test-delegate-key"


def archive_in(path: Path) -> SessionLogArchive:
    """An archive whose transcripts and copies are both under ``path``."""
    return SessionLogArchive(path / "session-logs", path / "projects")


def write_transcript(archive: SessionLogArchive, session: str, text: str) -> None:
    source = archive.transcript_root / "-dev-northwind-alpha" / f"{session}.jsonl"
    (source.parent / session).mkdir(parents=True, exist_ok=True)
    source.write_text(text)
    (source.parent / session / "agent-1.jsonl").write_text("sub\n")


def archived(archive: SessionLogArchive, session: str, task: str = "NORT-7") -> Path:
    return archive.task_dir("northwind", task) / f"{session}.jsonl"


async def until_file(path: Path, text: str, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not (path.is_file() and path.read_text() == text):
            await asyncio.sleep(0.02)


def test_an_agent_exit_archives_the_session_logs_of_its_task(store, tmp_path):
    logs = archive_in(tmp_path)
    harness = Harness(store, session_logs=logs)
    session = session_of("NORT-7")
    harness.add_task("NORT-7", session=session)
    harness.add_task("NORT-8", session=session_of("NORT-8"))
    write_transcript(logs, session_of("NORT-8"), "eight\n")
    harness.daemon.rows["ag1"] = agent_row(session=session)

    async def scenario():
        async with harness.client() as api:
            await until(api, "/api/agents", lambda b: b["agents"][0]["taskId"])
            # NORT-8's copy says the start's sweep is done, so only the exit
            # can copy NORT-7's session.
            await until_file(archived(logs, session_of("NORT-8"), "NORT-8"), "eight\n")
            write_transcript(logs, session, "the whole session\n")
            harness.daemon.rows["ag1"]["state"] = "exited(0)"
            await until_file(archived(logs, session), "the whole session\n")

    run(scenario())


def test_the_sweep_at_start_archives_every_session_of_every_task(store, tmp_path):
    logs = archive_in(tmp_path)
    harness = Harness(store, session_logs=logs)
    harness.add_task("NORT-7", session=session_of("NORT-7"))
    harness.add_task("NORT-8", session=session_of("NORT-8"))
    write_transcript(logs, session_of("NORT-7"), "seven\n")
    write_transcript(logs, session_of("NORT-8"), "eight\n")

    async def scenario():
        async with harness.client():
            await until_file(archived(logs, session_of("NORT-7")), "seven\n")
            await until_file(archived(logs, session_of("NORT-8"), "NORT-8"), "eight\n")

    run(scenario())


def test_a_delegated_agents_session_log_is_stored_on_its_parent(
    store, tmp_path, monkeypatch
):
    """The exit pushes it. A push the parent did not take is sent by the next
    sweep, and a sweep pushes again only after the source changes."""
    pushes = _count_calls(
        monkeypatch,
        RemoteTaskSource,
        "store_session_log",
        first_raises=TaskSourceRemote("the parent is down"),
    )
    parent_logs = archive_in(tmp_path / "parent")
    child_logs = archive_in(tmp_path / "child")

    async def scenario():
        async with cobalt_and_parent(store, tmp_path, session_logs=parent_logs) as (
            child,
            parent,
            api,
            _,
        ):
            # Set after the child's start, so the child runs no sweep of its
            # own: the test drives each pass.
            child.harness.orch.session_logs = child_logs
            await parent.add_task_async("NORT-7", session=session_of("NORT-7"))
            await until(api, "/api/tasks", has_task(TASK))
            await operated(api, f"/api/tasks/{TASK}/launch", {"host": "cobalt"})
            await merged(api, "cobalt:new1", lambda a: a["taskId"])
            agent = child.harness.orch.world["agents"]["cobalt:new1"]
            session = agent["session"]
            write_transcript(child_logs, session, "delegated\n")

            child.claude.rows["new1"]["state"] = "exited(0)"
            await child.harness.orch.refresh_agents()
            async with asyncio.timeout(5):
                while not pushes:
                    await asyncio.sleep(0.02)
            assert not archived(parent_logs, session).exists()

            await child.harness.orch.archive_all_session_logs()
            await until_file(archived(parent_logs, session), "delegated\n")
            sub = (
                parent_logs.task_dir("northwind", "NORT-7") / session / "agent-1.jsonl"
            )
            assert sub.read_text() == "sub\n"
            assert len(pushes) == 2

            await child.harness.orch.archive_all_session_logs()
            assert len(pushes) == 2
            write_transcript(child_logs, session, "delegated\nresumed\n")
            await child.harness.orch.archive_all_session_logs()
            await until_file(archived(parent_logs, session), "delegated\nresumed\n")

    run(scenario())
    assert len(pushes) == 3


OTHER_TASK_KEY = "mael_test-other-delegate-key"


@pytest.mark.parametrize(
    ("key", "session", "status", "code"),
    [
        ("mael_test-host-key", session_of("NORT-7"), 403, "forbidden"),
        (OTHER_TASK_KEY, session_of("NORT-7"), 403, "forbidden"),
        (DELEGATE_KEY, "session-not-on-the-task", 404, "unknown_id"),
        (DELEGATE_KEY, session_of("NORT-7"), 404, "unknown_id"),
    ],
    ids=["host-key", "another-tasks-key", "not-on-the-task", "not-the-keys-agent"],
)
def test_a_session_log_push_is_refused_without_its_agents_delegate_key(
    store, tmp_path, key, session, status, code
):
    logs = archive_in(tmp_path)
    harness = Harness(store, session_logs=logs)
    harness.add_task("NORT-7", session=session_of("NORT-7"))
    write_transcript(logs, session_of("NORT-7"), "seven\n")
    pack = logs.pack(SessionRef(session_of("NORT-7")))
    # The keys' agent runs another session than the one pushed.
    harness.daemon.rows["ag1"] = agent_row(session="session-of-ag1")

    async def scenario():
        async with harness.client() as api:
            # After the start, whose sweep drops a key of a host not in `hosts:`.
            for secret, task in (
                (DELEGATE_KEY, TASK),
                (OTHER_TASK_KEY, "northwind/NORT-8"),
            ):
                await harness.auth.save_api_key(
                    ApiKey(hash_secret(secret), DELEGATE, NOW, task, "cobalt", "ag1")
                )
            async with api.session.put(
                f"/api/tasks/{TASK}/session-logs/{session}",
                data=pack,
                headers={"Authorization": f"Bearer {key}"},
            ) as response:
                return response.status, await response.json()

    got_status, body = run(scenario())
    assert (got_status, body["error"]["code"]) == (status, code)
