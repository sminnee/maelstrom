"""The daemon's command surface, driven with a stub child instead of a subprocess."""

import asyncio
import json
import logging
import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maelstrom import agent_server
from maelstrom.agent_model import (
    AGENT_DETAIL,
    AGENT_EXITED,
    BACKLOG_END,
    DEFAULT_RESUME_PROMPT,
    EXITED,
    INTERRUPTED_REASON,
    PROCESSING,
    SPEC_EXITED,
    SPEC_STOPPED,
    TRUNCATED,
    AgentSpec,
    TranscriptMeta,
    apply_event,
    mark_exited,
    user_message,
)
from maelstrom.agent_server import Agent, AgentDaemon
from maelstrom.agent_spec_store import (
    InMemoryAgentSpecStore,
    JsonAgentSpecStore,
)
from maelstrom.session_discovery import LiveSessionSet
from maelstrom.task_index import TaskMeta
from maelstrom.transcript_store import InMemoryTranscriptStore

FIXTURES = Path(__file__).parent / "fixtures" / "agent_events"


def replay(name: str, stop_before_control: bool = False):
    """Feed one fixture through the reducer and return the final state."""
    from maelstrom.agent_model import AgentState

    state = AgentState(agent_id="a1", cwd="/tmp/x")
    for line in (FIXTURES / name).read_text().splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        state = apply_event(state, event)
        if stop_before_control and event.get("type") == "control_request":
            break
    return state


def _stub_agent(agent_id: str = "a1") -> Agent:
    """An `Agent` with a stub child, so `handle` is testable with no subprocess."""
    proc = MagicMock()
    proc.stdin.is_closing.return_value = True
    return Agent(agent_id, "/tmp/x", proc)


class _RecordingWriter:
    """A stand-in for ``StreamWriter`` that keeps every line written to it."""

    def __init__(self) -> None:
        self.lines: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.lines.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


def _recording_writer() -> _RecordingWriter:
    return _RecordingWriter()


async def _handle(daemon: AgentDaemon, payload: dict) -> dict:
    return await daemon.handle(payload)


def test_handle_rejects_an_unknown_agent():
    reply = asyncio.run(
        _handle(AgentDaemon("/tmp/x.sock"), {"cmd": "say", "id": "nope"})
    )
    assert "no such agent" in reply["error"]


def test_handle_rejects_an_unknown_command():
    daemon = AgentDaemon("/tmp/x.sock")
    daemon.agents["a1"] = _stub_agent()
    reply = asyncio.run(_handle(daemon, {"cmd": "wat", "id": "a1"}))
    assert "unknown command" in reply["error"]


def test_ping_says_which_daemon_is_answering():
    """`ping` is what makes one daemon of several identifiable.

    It carries no agent id, so it answers before the agent lookup and works on
    a daemon holding nothing.
    """
    daemon = AgentDaemon("/tmp/x.sock")
    reply = asyncio.run(_handle(daemon, {"cmd": "ping"}))
    identity = reply["daemon"]
    assert identity["socket_path"] == "/tmp/x.sock"
    assert identity["pid"] == os.getpid()
    assert identity["agents"] == 0
    # The tree the serving code was imported from — the question `ping` exists
    # to answer when a stale daemon from another worktree holds the socket.
    assert identity["source_tree"] == str(Path(agent_server.__file__).parents[2])


def test_shutdown_asks_the_daemon_to_stop_serving():
    """`mael agent daemon stop` needs a way in: the socket, not a signal.

    The reply comes back before the daemon goes, so the caller learns it was
    heard rather than seeing a closed connection.
    """
    daemon = AgentDaemon("/tmp/x.sock")
    reply = asyncio.run(_handle(daemon, {"cmd": "shutdown"}))
    assert reply == {"ok": True}
    assert daemon.stopping.is_set()


def test_ping_names_the_spawn_record_directory():
    """A test daemon and the real one differ by spec dir, so `ping` reports it."""
    daemon = AgentDaemon("/tmp/x.sock", JsonAgentSpecStore(Path("/tmp/specs")))
    assert asyncio.run(_handle(daemon, {"cmd": "ping"}))["daemon"]["spec_dir"] == (
        "/tmp/specs"
    )


def test_ping_counts_the_agents_it_holds():
    daemon = AgentDaemon("/tmp/x.sock")
    daemon.agents["a1"] = _stub_agent()
    reply = asyncio.run(_handle(daemon, {"cmd": "ping"}))
    assert reply["daemon"]["agents"] == 1


def test_handle_refuses_to_answer_an_agent_that_is_not_waiting():
    daemon = AgentDaemon("/tmp/x.sock")
    daemon.agents["a1"] = _stub_agent()
    reply = asyncio.run(_handle(daemon, {"cmd": "approve", "id": "a1"}))
    assert "not waiting" in reply["error"]


def test_handle_refuses_every_command_against_an_exited_agent():
    """Answering a dead agent must fail loudly, not report a silent success."""
    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    agent.state = mark_exited(agent.state, 1)
    daemon.agents["a1"] = agent
    reply = asyncio.run(_handle(daemon, {"cmd": "say", "id": "a1", "text": "hi"}))
    assert "has exited" in reply["error"]


def test_handle_refuses_to_answer_a_wait_that_is_not_a_question():
    """`answer` on a plan review would send an empty answers map, reading as no answer."""
    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    agent.state = replay("plan-review.jsonl", stop_before_control=True)
    daemon.agents["a1"] = agent
    reply = asyncio.run(_handle(daemon, {"cmd": "answer", "id": "a1", "choice": "yes"}))
    assert "not waiting on a question" in reply["error"]


def test_handle_lists_every_agent():
    daemon = AgentDaemon("/tmp/x.sock")
    daemon.agents["a1"] = _stub_agent()
    reply = asyncio.run(_handle(daemon, {"cmd": "list"}))
    assert [row["id"] for row in reply["agents"]] == ["a1"]


def test_show_returns_one_agents_detail():
    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    agent.state = replay("question-unanswered.jsonl", stop_before_control=True)
    daemon.agents["a1"] = agent
    reply = asyncio.run(_handle(daemon, {"cmd": "show", "id": "a1"}))
    assert reply["agent"]["waiting_tool"] == "AskUserQuestion"
    assert reply["agent"]["questions"][0]["options"][0]["label"] == "Red"


def test_show_works_on_an_exited_agent():
    """Inspecting why an agent died is the main reason to run ``show``."""
    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    agent.state = mark_exited(replay("normal-turn.jsonl"), 1)
    daemon.agents["a1"] = agent
    reply = asyncio.run(_handle(daemon, {"cmd": "show", "id": "a1"}))
    assert reply["agent"]["state"] == "exited(1)"
    assert reply["agent"]["message"] == "Hello there, friend"


def test_show_rejects_an_unknown_agent():
    reply = asyncio.run(
        _handle(AgentDaemon("/tmp/x.sock"), {"cmd": "show", "id": "nope"})
    )
    assert "no such agent" in reply["error"]


def test_attach_marks_where_the_backlog_ends():
    """``tail`` without ``-f`` needs to know when history stops, not guess."""
    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    agent.state = replay("normal-turn.jsonl")
    daemon.agents["a1"] = agent
    writer = _recording_writer()

    async def attach_then_disconnect():
        task = asyncio.create_task(daemon._attach("a1", writer))
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(attach_then_disconnect())
    kinds = [json.loads(line).get("type") for line in writer.lines]
    assert kinds[-1] == BACKLOG_END
    assert kinds.count(BACKLOG_END) == 1
    assert "result" in kinds


def test_attach_still_marks_the_end_for_an_agent_that_said_nothing():
    daemon = AgentDaemon("/tmp/x.sock")
    daemon.agents["a1"] = _stub_agent()
    writer = _recording_writer()

    async def attach_then_disconnect():
        task = asyncio.create_task(daemon._attach("a1", writer))
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(attach_then_disconnect())
    assert [json.loads(line).get("type") for line in writer.lines] == [
        AGENT_DETAIL,
        BACKLOG_END,
    ]


# --- the three additions the orchestrator server relies on --------------------


def test_start_merges_env_over_the_daemons_own_environment(monkeypatch):
    """Without ``env`` reaching the child, ``MAEL_TASK_ID`` never reaches its skills."""
    from unittest.mock import AsyncMock, patch

    from maelstrom import agent_server

    monkeypatch.setenv("INHERITED", "yes")
    proc = MagicMock()
    proc.stdin.is_closing.return_value = True
    proc.stdout.readline = AsyncMock(return_value=b"")
    proc.wait = AsyncMock(return_value=0)
    daemon, _ = _daemon_with_specs()
    with patch.object(
        agent_server.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    ) as spawn:
        asyncio.run(
            _handle(
                daemon,
                {"cmd": "start", "cwd": "/tmp/x", "env": {"MAEL_TASK_ID": "T-1"}},
            )
        )
    env = spawn.call_args.kwargs["env"]
    assert env["MAEL_TASK_ID"] == "T-1"
    assert env["INHERITED"] == "yes"


def test_a_watcher_is_told_when_the_agent_exits():
    """The attach stream ends with the exit marker, not with a silent hang."""
    from unittest.mock import AsyncMock

    from maelstrom.agent_model import AGENT_EXITED

    proc = MagicMock()
    proc.stdin.is_closing.return_value = True
    proc.stdout.readline = AsyncMock(return_value=b"")
    proc.wait = AsyncMock(return_value=3)
    daemon = AgentDaemon("/tmp/x.sock")
    agent = Agent("a1", "/tmp/x", proc)
    daemon.agents["a1"] = agent
    writer = _recording_writer()

    async def attach_then_exit():
        attached = asyncio.create_task(daemon._attach("a1", writer))
        await asyncio.sleep(0)
        await agent.pump()
        await asyncio.wait_for(attached, timeout=2)

    asyncio.run(attach_then_exit())
    events = [json.loads(line) for line in writer.lines]
    assert events[-1] == {"type": AGENT_EXITED, "exit_code": 3}
    assert events[-2]["type"] == BACKLOG_END


def test_an_over_long_line_does_not_stop_the_pump(caplog):
    """A line past the read limit costs that one event, not the whole agent."""
    proc = MagicMock()
    proc.stdin.is_closing.return_value = True
    proc.returncode = 0
    proc.stdout.readline = AsyncMock(
        side_effect=[
            b'{"type":"system","subtype":"init","hook_id":"one"}\n',
            ValueError("Separator is found, but chunk is longer than limit"),
            b'{"type":"system","subtype":"init","hook_id":"two"}\n',
            b"",
        ]
    )
    proc.wait = AsyncMock(return_value=0)
    daemon = AgentDaemon("/tmp/x.sock")
    agent = Agent("a1", "/tmp/x", proc)
    daemon.agents["a1"] = agent
    writer = _recording_writer()

    async def attach_then_pump():
        attached = asyncio.create_task(daemon._attach("a1", writer))
        await asyncio.sleep(0)
        await agent.pump()
        await asyncio.wait_for(attached, timeout=2)

    with caplog.at_level(logging.WARNING, logger=agent_server.__name__):
        asyncio.run(asyncio.wait_for(attach_then_pump(), timeout=5))

    assert [e.get("hook_id") for e in agent.state.recent] == ["one", "two"]
    assert agent.state.status == EXITED
    proc.kill.assert_not_called()
    assert "skipping a line over" in caplog.text
    # The gap reaches the watcher, so a dropped event is not silent.
    kinds = [json.loads(line).get("type") for line in writer.lines]
    assert TRUNCATED in kinds


def test_a_pump_that_fails_unexpectedly_still_settles_the_agent():
    """An unexpected failure ends the agent rather than freezing it.

    The child is still running, so the exit wait needs its bound to return.
    """
    live = asyncio.Event()  # never set: the child outlives its pump
    proc = MagicMock()
    proc.stdin.is_closing.return_value = True
    proc.returncode = None
    proc.stdout.readline = AsyncMock(side_effect=RuntimeError("the reader broke"))
    proc.wait = AsyncMock(side_effect=live.wait)
    daemon = AgentDaemon("/tmp/x.sock")
    agent = Agent("a1", "/tmp/x", proc)
    daemon.agents["a1"] = agent
    waiters: list[asyncio.Future] = []

    async def pump_and_watch():
        attached = asyncio.create_task(daemon._attach("a1", writer))
        await asyncio.sleep(0)
        waiter = asyncio.get_running_loop().create_future()
        agent.waiting["r1"] = waiter
        waiters.append(waiter)
        with patch.object(agent_server, "EXIT_WAIT", 0.05):
            with pytest.raises(RuntimeError):
                await agent.pump()
        await asyncio.wait_for(attached, timeout=2)

    writer = _recording_writer()
    asyncio.run(asyncio.wait_for(pump_and_watch(), timeout=5))

    assert agent.state.status == EXITED
    assert isinstance(waiters[0].exception(), ConnectionResetError)
    assert json.loads(writer.lines[-1])["type"] == AGENT_EXITED
    proc.kill.assert_called_once()


def test_stop_kills_only_a_child_that_ignores_its_closed_stdin():
    """Closing stdin is what a child is meant to notice, so it goes first."""
    quiet = MagicMock()
    quiet.stdin.is_closing.return_value = False
    quiet.returncode = None
    quiet.wait = AsyncMock(return_value=0)
    asyncio.run(asyncio.wait_for(Agent("a1", "/tmp/x", quiet).stop(), timeout=5))
    quiet.stdin.close.assert_called_once()
    quiet.kill.assert_not_called()

    stubborn = MagicMock()
    stubborn.stdin.is_closing.return_value = False
    stubborn.returncode = None
    stubborn.wait = AsyncMock(side_effect=asyncio.Event().wait)
    with patch.object(agent_server, "EXIT_WAIT", 0.05):
        asyncio.run(asyncio.wait_for(Agent("a2", "/tmp/x", stubborn).stop(), timeout=5))
    stubborn.kill.assert_called_once()


def test_a_cancelled_pump_leaves_the_child_to_stop():
    """Loop teardown is not a crash, so the child outlives its pump.

    A killed child would be recorded as exited, and `restore` brings back only
    the records still marked running — so a restart would silently drop it.
    """
    started = asyncio.Event()

    async def block():
        started.set()
        await asyncio.Event().wait()  # never returns: the child is quiet
        return b""

    proc = MagicMock()
    proc.stdin.is_closing.return_value = True
    proc.returncode = None
    proc.stdout.readline = AsyncMock(side_effect=block)
    proc.wait = AsyncMock(return_value=0)
    agent = Agent("a1", "/tmp/x", proc)

    async def cancel_mid_read():
        pump = asyncio.create_task(agent.pump())
        await started.wait()
        pump.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pump

    asyncio.run(asyncio.wait_for(cancel_mid_read(), timeout=5))

    proc.kill.assert_not_called()


def test_the_spawn_bounds_the_childs_line_length():
    """The child's stdout gets the same limit as every socket the daemon reads."""
    proc = MagicMock()
    proc.stdin.is_closing.return_value = True
    proc.stdout.readline = AsyncMock(return_value=b"")
    proc.wait = AsyncMock(return_value=0)
    daemon, _ = _daemon_with_specs()
    with patch.object(
        agent_server.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    ) as spawn:
        asyncio.run(_handle(daemon, {"cmd": "start", "cwd": "/tmp/x"}))
    assert spawn.call_args.kwargs["limit"] == agent_server.STREAM_LIMIT


def test_attaching_to_an_exited_agent_ends_after_the_backlog():
    from maelstrom.agent_model import AGENT_EXITED

    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    agent.state = mark_exited(replay("normal-turn.jsonl"), 0)
    daemon.agents["a1"] = agent
    writer = _recording_writer()
    asyncio.run(asyncio.wait_for(daemon._attach("a1", writer), timeout=2))
    kinds = [json.loads(line).get("type") for line in writer.lines]
    assert kinds[-2:] == [BACKLOG_END, AGENT_EXITED]
    assert json.loads(writer.lines[-1])["exit_code"] == 0


def test_answer_accepts_a_map_of_answers_keyed_by_question():
    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    agent.state = replay("question-unanswered.jsonl", stop_before_control=True)
    sent: list[dict] = []

    async def record(message: dict) -> bool:
        sent.append(message)
        return True

    agent.send = record  # type: ignore[method-assign]
    daemon.agents["a1"] = agent
    answers = {"Which colour do you prefer?": "Blue"}
    reply = asyncio.run(
        _handle(daemon, {"cmd": "answer", "id": "a1", "answers": answers})
    )
    assert reply == {"ok": True}
    assert sent[0]["response"]["response"]["updatedInput"]["answers"] == answers


def test_answer_refuses_an_empty_answer_map():
    """An empty map reads as no answer at all, so it must not resolve the wait."""
    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    agent.state = replay("question-unanswered.jsonl", stop_before_control=True)
    daemon.agents["a1"] = agent
    reply = asyncio.run(_handle(daemon, {"cmd": "answer", "id": "a1", "answers": {}}))
    assert "no answers" in reply["error"]


def test_answer_with_neither_answers_nor_choice_is_an_error_reply():
    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    agent.state = replay("question-unanswered.jsonl", stop_before_control=True)
    daemon.agents["a1"] = agent
    reply = asyncio.run(_handle(daemon, {"cmd": "answer", "id": "a1"}))
    assert "no answer" in reply["error"]


# --- spawn records: surviving a crash ---------------------------------------


def _spawn_stub(exit_code: int = 0):
    """A stub child whose stream is already at its end but whose stdin accepts writes.

    ``Agent.send`` drops a message on a closing stdin, so a stub that reports
    one closed would swallow the very prompt these tests assert on.
    """
    proc = MagicMock()
    proc.stdin.is_closing.return_value = False
    proc.stdin.drain = AsyncMock(return_value=None)
    proc.stdout.readline = AsyncMock(return_value=b"")
    proc.wait = AsyncMock(return_value=exit_code)
    return proc


def _daemon_with_specs(*, has_transcript: bool = True):
    """A daemon whose records live in memory, so no file is touched.

    ``has_transcript`` stands in for the session transcript on disk, which is
    what decides whether a resume replays or starts fresh.
    """
    specs = InMemoryAgentSpecStore()
    daemon = AgentDaemon(
        "/tmp/x.sock", specs=specs, has_transcript=lambda path, sid: has_transcript
    )
    return daemon, specs


class _FakeTaskIndex:
    """A ``TaskLookup`` that counts how many times the listing opened it."""

    opened = 0

    def __init__(self, tasks: dict[str, str]):
        self._tasks = tasks

    def find_by_session_id(self, session_id: str):
        task_id = self._tasks.get(session_id)
        return TaskMeta(project="p", id=task_id, status="done") if task_id else None


def _stopped_daemon(metas, *, live=None, tasks=None, records=True):
    """A daemon whose stopped listing reads injected transcripts, not the disk.

    Each transcript gets a spawn record by default, because only a session with
    one is listed. ``records=False`` leaves them off, to test that.
    """
    transcripts = InMemoryTranscriptStore()
    specs = InMemoryAgentSpecStore()
    for meta in metas:
        transcripts.add_meta(meta)
        if records:
            specs.write(
                AgentSpec(
                    agent_id=f"rec-{meta.session_id}",
                    cwd=str(meta.cwd),
                    session_id=meta.session_id,
                    status=SPEC_STOPPED,
                )
            )
    opens = []

    def open_index():
        opens.append(1)
        return _FakeTaskIndex(tasks or {})

    daemon = AgentDaemon(
        "/tmp/x.sock",
        specs=specs,
        has_transcript=lambda path, sid: True,
        transcripts=transcripts,
        live=LiveSessionSet(sessions=live or []),
        open_task_index=open_index,
    )
    daemon._test_opens = opens
    return daemon, specs


def _meta(session_id="s1", cwd="/tmp/x", **kw):
    fields = {"session_id": session_id, "cwd": Path(cwd), "modified_at": 1.0}
    fields.update(kw)
    return TranscriptMeta(**fields)


def test_the_stopped_scope_lists_a_transcript_the_default_scope_hides():
    daemon, _ = _stopped_daemon([_meta("s1")])
    assert asyncio.run(_handle(daemon, {"cmd": "list"}))["agents"] == []
    rows = asyncio.run(_handle(daemon, {"cmd": "list", "scope": "stopped"}))["agents"]
    assert [row["session"] for row in rows] == ["s1"]


def test_the_all_scope_lists_the_running_agents_and_the_stopped_ones():
    daemon, _ = _stopped_daemon([_meta("s1")])
    _spawning(daemon, [{"cmd": "start", "cwd": "/tmp/x"}])
    running = next(iter(daemon.agents))
    rows = asyncio.run(_handle(daemon, {"cmd": "list", "scope": "all"}))["agents"]
    # A running row is keyed by agent id; a stopped one names its session too.
    assert {row["id"] for row in rows if "state" in row} == {running}
    assert {row["session"] for row in rows if "state" not in row} == {"s1"}


def test_an_unknown_scope_is_refused_rather_than_silently_read_as_running():
    """A typo must not quietly return the default listing."""
    daemon, _ = _stopped_daemon([])
    reply = asyncio.run(_handle(daemon, {"cmd": "list", "scope": "stoped"}))
    assert "scope" in reply["error"]


def test_the_stopped_scope_filters_on_the_cwd_the_client_sends():
    """The CLI resolves a worktree to a path; the daemon never learns of projects."""
    daemon, _ = _stopped_daemon(
        [_meta("s1", cwd="/w/alpha"), _meta("s2", cwd="/w/bravo")]
    )
    rows = asyncio.run(
        _handle(daemon, {"cmd": "list", "scope": "stopped", "cwd": "/w/alpha"})
    )["agents"]
    assert [row["session"] for row in rows] == ["s1"]


def test_the_stopped_scope_carries_the_record_of_an_agent_that_was_stopped():
    """The end-to-end point of slice 1: a stop keeps what the resume needs."""
    daemon, specs = _stopped_daemon([_meta("sid-1", cwd="/tmp/x")], records=False)
    _spawning(
        daemon,
        [{"cmd": "start", "cwd": "/tmp/x", "model": "opus", "session": "sid-1"}],
    )
    agent_id = next(iter(daemon.agents))
    asyncio.run(_handle(daemon, {"cmd": "stop", "id": agent_id}))
    (row,) = asyncio.run(_handle(daemon, {"cmd": "list", "scope": "stopped"}))["agents"]
    assert row["model"] == "opus"


def _spawning(daemon, payloads, *, proc=None, returning=False):
    """Run ``payloads`` through ``handle`` with a stubbed spawn.

    Returns the spawn mock, or the last reply when ``returning`` is set.
    """
    child = proc if proc is not None else _spawn_stub()

    replies = []

    async def run():
        for payload in payloads:
            replies.append(await daemon.handle(payload))
        # The pump task marks the agent exited; let it finish before asserting.
        await asyncio.gather(
            *(a.pump_task for a in daemon.agents.values() if a.pump_task),
            return_exceptions=True,
        )

    with patch.object(
        agent_server.asyncio, "create_subprocess_exec", AsyncMock(return_value=child)
    ) as spawn:
        asyncio.run(run())
    return replies[-1] if returning else spawn


def test_start_writes_a_record_with_a_session_id_it_minted():
    """A child that dies before ``system/init`` still has an id to resume."""
    daemon, specs = _daemon_with_specs()
    _spawning(daemon, [{"cmd": "start", "cwd": "/tmp/x", "prompt": "go"}])
    (spec,) = specs.list()
    assert spec.cwd == "/tmp/x"
    assert spec.prompt == "go"
    assert spec.session_id  # minted, because the caller gave none


def test_start_keeps_the_session_id_the_caller_pinned():
    daemon, specs = _daemon_with_specs()
    _spawning(daemon, [{"cmd": "start", "cwd": "/tmp/x", "session": "sid-1"}])
    (spec,) = specs.list()
    assert spec.session_id == "sid-1"


def test_start_keeps_the_opening_prompt_for_a_child_that_dies_before_its_turn():
    """Such a child wrote no transcript, so its resume needs the prompt again."""
    daemon, specs = _daemon_with_specs()
    _spawning(daemon, [{"cmd": "start", "cwd": "/tmp/x", "prompt": "go"}])
    (spec,) = specs.list()
    assert spec.prompt == "go"


def test_start_scrubs_the_markers_that_suppress_a_transcript(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    monkeypatch.setenv("CLAUDECODE", "1")
    daemon, _ = _daemon_with_specs()
    spawn = _spawning(daemon, [{"cmd": "start", "cwd": "/tmp/x"}])
    env = spawn.call_args.kwargs["env"]
    assert "CLAUDE_CODE_CHILD_SESSION" not in env
    assert env["CLAUDE_CODE_FORCE_SESSION_PERSISTENCE"] == "1"


def test_a_child_that_ends_leaves_an_exited_record():
    """An exit written to disk survives the daemon that observed it."""
    daemon, specs = _daemon_with_specs()
    _spawning(
        daemon, [{"cmd": "start", "cwd": "/tmp/x"}], proc=_spawn_stub(exit_code=-9)
    )
    (spec,) = specs.list()
    assert spec.status == "exited"
    assert spec.exit_code == -9


def test_stop_keeps_the_record_and_marks_it_stopped():
    """A stopped agent stays resumable, so its record must survive the stop.

    The record is the only thing that knows the model, the permission mode and
    the env the resume needs; a transcript knows none of them.
    """
    daemon, specs = _daemon_with_specs()
    _spawning(daemon, [{"cmd": "start", "cwd": "/tmp/x", "model": "opus"}])
    agent_id = next(iter(daemon.agents))
    asyncio.run(_handle(daemon, {"cmd": "stop", "id": agent_id}))
    (spec,) = specs.list()
    assert spec.status == SPEC_STOPPED
    assert spec.model == "opus"


def test_a_stopped_agent_is_absent_from_the_default_list():
    """The orchestrator infers an exit from absence, and must keep doing so.

    ``docs/dev/orchestrator-server.md``: an id that is gone has exited. A stopped
    agent appearing in the default list would sit on the canvas for ever, and
    ``mael agent list`` would grow without bound.
    """
    daemon, _ = _daemon_with_specs()
    _spawning(daemon, [{"cmd": "start", "cwd": "/tmp/x"}])
    agent_id = next(iter(daemon.agents))
    asyncio.run(_handle(daemon, {"cmd": "stop", "id": agent_id}))
    reply = asyncio.run(_handle(daemon, {"cmd": "list"}))
    assert reply["agents"] == []


def test_restore_leaves_a_stopped_record_out_of_the_listing():
    """A stop is deliberate, so a later daemon must neither respawn nor show it."""
    daemon, specs = _daemon_with_specs()
    specs.write(
        AgentSpec(
            agent_id="gone",
            cwd="/tmp/x",
            session_id="s1",
            status=SPEC_STOPPED,
        )
    )
    with patch.object(
        agent_server.asyncio, "create_subprocess_exec", AsyncMock()
    ) as spawn:
        asyncio.run(daemon.restore())
    assert spawn.call_count == 0
    assert daemon.agents == {}


def test_resume_replays_the_session_and_keeps_the_agent_id():
    """The id the orchestrator and the user know must not change on a resume."""
    daemon, specs = _daemon_with_specs()
    specs.write(
        AgentSpec(
            agent_id="a1",
            cwd="/tmp/x",
            session_id="sid-1",
            permission_mode="auto",
            model="opus",
            env={"MAEL_TASK_ID": "T-1"},
            prompt="go",
            status="exited",
            exit_code=-9,
        )
    )
    daemon.agents["a1"] = _stub_agent()
    daemon.agents["a1"].state = mark_exited(daemon.agents["a1"].state, -9)

    spawn = _spawning(daemon, [{"cmd": "resume", "id": "a1"}])
    argv = list(spawn.call_args.args)
    assert argv[argv.index("--resume") + 1] == "sid-1"
    assert "--session-id" not in argv
    assert argv[argv.index("--permission-mode") + 1] == "auto"
    assert spawn.call_args.kwargs["env"]["MAEL_TASK_ID"] == "T-1"
    assert "a1" in daemon.agents
    sent = spawn.return_value.stdin.write.call_args.args[0].decode()
    assert DEFAULT_RESUME_PROMPT in sent


def test_a_resume_after_set_mode_normal_omits_the_flag():
    """`normal` is the absence of the flag, so the argv must not carry the word.

    `claude --permission-mode normal` is refused outright, and `restore` would
    then record the agent `exited` — losing the agent the spawn-record write
    exists to keep.
    """
    daemon, specs = _daemon_with_specs()
    specs.write(
        AgentSpec(
            agent_id="a1",
            cwd="/tmp/x",
            session_id="sid-1",
            permission_mode="normal",
            status="exited",
            exit_code=-9,
        )
    )
    daemon.agents["a1"] = _stub_agent()
    daemon.agents["a1"].state = mark_exited(daemon.agents["a1"].state, -9)

    spawn = _spawning(daemon, [{"cmd": "resume", "id": "a1"}])
    argv = list(spawn.call_args.args)
    assert "--permission-mode" not in argv


def test_resume_of_a_child_that_never_got_its_prompt_starts_it_fresh():
    """No prompt means no transcript, so ``--resume`` would have nothing to replay."""
    daemon, specs = _daemon_with_specs(has_transcript=False)
    specs.write(
        AgentSpec(
            agent_id="a1",
            cwd="/tmp/x",
            session_id="sid-1",
            prompt="the original prompt",
            status="exited",
        )
    )
    daemon.agents["a1"] = _stub_agent()
    daemon.agents["a1"].state = mark_exited(daemon.agents["a1"].state, 1)

    spawn = _spawning(daemon, [{"cmd": "resume", "id": "a1"}])
    argv = list(spawn.call_args.args)
    assert argv[argv.index("--session-id") + 1] == "sid-1"
    sent = spawn.return_value.stdin.write.call_args.args[0].decode()
    assert "the original prompt" in sent


def test_resume_sends_the_text_the_caller_gave():
    daemon, specs = _daemon_with_specs()
    specs.write(
        AgentSpec(
            agent_id="a1",
            cwd="/tmp/x",
            session_id="sid-1",
            status="exited",
        )
    )
    daemon.agents["a1"] = _stub_agent()
    daemon.agents["a1"].state = mark_exited(daemon.agents["a1"].state, 1)
    spawn = _spawning(daemon, [{"cmd": "resume", "id": "a1", "text": "carry on"}])
    sent = spawn.return_value.stdin.write.call_args.args[0].decode()
    assert "carry on" in sent


def test_resume_refuses_an_agent_that_is_still_running():
    """Two children on one session id would fight over the same transcript."""
    daemon, _ = _daemon_with_specs()
    daemon.agents["a1"] = _stub_agent()
    reply = asyncio.run(_handle(daemon, {"cmd": "resume", "id": "a1"}))
    assert "is running" in reply["error"]


def test_resume_rejects_an_agent_it_has_no_record_of():
    daemon, _ = _daemon_with_specs()
    reply = asyncio.run(_handle(daemon, {"cmd": "resume", "id": "nope"}))
    assert "no such agent" in reply["error"]


def test_restore_respawns_only_the_records_still_marked_running():
    """A daemon start brings back what was running and leaves the rest alone."""
    daemon, specs = _daemon_with_specs()
    specs.write(
        AgentSpec(
            agent_id="live",
            cwd="/tmp/x",
            session_id="s1",
            status="running",
        )
    )
    specs.write(
        AgentSpec(
            agent_id="dead",
            cwd="/tmp/y",
            session_id="s2",
            status="exited",
            exit_code=2,
        )
    )

    proc = _spawn_stub()
    with patch.object(
        agent_server.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    ) as spawn:
        asyncio.run(daemon.restore())
    assert spawn.call_count == 1
    # Both are listable, so `list`, `show` and `resume` work after a restart.
    assert set(daemon.agents) == {"live", "dead"}
    assert daemon.agents["dead"].state.status == EXITED
    assert daemon.agents["dead"].state.exit_code == 2


def test_a_daemon_shutdown_leaves_its_records_resumable():
    """Restarting the daemon must not lose the agents it was holding.

    Shutdown stops every child, which ends its stream and would otherwise record
    an exit. An exit recorded here is indistinguishable from a crash, so the next
    daemon start would leave the agent alone instead of bringing it back.
    """
    daemon, specs = _daemon_with_specs()
    # A child whose stream is still open at shutdown, so its pump is live and
    # its `finally` runs during the stop — which is when the exit was recorded.
    stream_open: asyncio.Event | None = None
    proc = MagicMock()
    proc.stdin.is_closing.return_value = False
    proc.stdin.drain = AsyncMock(return_value=None)
    proc.wait = AsyncMock(return_value=0)

    async def scenario():
        nonlocal stream_open
        stream_open = asyncio.Event()

        async def read_until_ended() -> bytes:
            """Ends the stream only when the test says the child has gone."""
            assert stream_open is not None
            await stream_open.wait()
            return b""

        proc.stdout.readline = read_until_ended
        with patch.object(
            agent_server.asyncio,
            "create_subprocess_exec",
            AsyncMock(return_value=proc),
        ):
            await daemon.handle({"cmd": "start", "cwd": "/tmp/x", "prompt": "go"})
        assert specs.list()[0].status == "running"
        stream_open.set()  # the child's stream ends, as `stop` would end it
        await daemon.shutdown()

    asyncio.run(scenario())

    (spec,) = specs.list()
    assert spec.status == "running"


def test_one_record_that_will_not_start_does_not_stop_the_daemon():
    """A daemon that cannot restore one agent must still serve the others.

    ``restore`` runs before the socket binds, so an exception escaping it loses
    every agent, not just the one whose record is bad.
    """
    daemon, specs = _daemon_with_specs()
    specs.write(
        AgentSpec(agent_id="bad", cwd="/gone", session_id="s1", status="running")
    )
    specs.write(
        AgentSpec(agent_id="good", cwd="/tmp/x", session_id="s2", status="running")
    )

    calls: list[str] = []

    async def spawn(*argv, **kwargs):
        calls.append(kwargs["cwd"])
        if kwargs["cwd"] == "/gone":
            raise ValueError("a record this daemon cannot use")
        return _spawn_stub()

    with patch.object(agent_server.asyncio, "create_subprocess_exec", spawn):
        asyncio.run(daemon.restore())

    assert "/tmp/x" in calls
    assert daemon.specs.read("bad").status == "exited"


def test_a_resume_with_no_record_says_so_rather_than_denying_the_agent():
    """A stop racing a resume must not claim an agent `list` just named."""
    daemon, _ = _daemon_with_specs()
    daemon.agents["a1"] = _stub_agent()
    daemon.agents["a1"].state = mark_exited(daemon.agents["a1"].state, 1)
    reply = asyncio.run(_handle(daemon, {"cmd": "resume", "id": "a1"}))
    assert reply["error"] == "agent a1 has no spawn record"


def test_a_resume_asks_the_transcript_not_only_the_record():
    """The record can say no prompt went out when one did.

    A daemon killed just after the prompt went out records nothing about it, so
    only the transcript knows the session started. Starting a session Claude
    already knows with ``--session-id`` is refused, which would make that agent
    unrecoverable.
    """
    daemon, specs = _daemon_with_specs()
    specs.write(
        AgentSpec(
            agent_id="a1",
            cwd="/tmp/x",
            session_id="sid-1",
            prompt="go",
            status="exited",
        )
    )
    daemon.agents["a1"] = _stub_agent()
    daemon.agents["a1"].state = mark_exited(daemon.agents["a1"].state, 1)

    spawn = _spawning(daemon, [{"cmd": "resume", "id": "a1"}])
    argv = list(spawn.call_args.args)
    assert argv[argv.index("--resume") + 1] == "sid-1"
    assert "--session-id" not in argv


# --- interrupt, and the replies the daemon writes back into the stream ------


def _sending_agent(agent_id: str = "a1") -> tuple[Agent, list[dict]]:
    """A stub agent whose ``send`` records the message instead of writing it."""
    agent = _stub_agent(agent_id)
    sent: list[dict] = []

    async def record(message: dict) -> bool:
        sent.append(message)
        return True

    agent.send = record  # type: ignore[method-assign]
    return agent, sent


def test_interrupt_sends_the_interrupt_control_request():
    daemon = AgentDaemon("/tmp/x.sock")
    agent, sent = _sending_agent()
    agent.state = replace(agent.state, status=PROCESSING)
    daemon.agents["a1"] = agent
    reply = asyncio.run(_handle(daemon, {"cmd": "interrupt", "id": "a1"}))
    assert reply == {"ok": True}
    assert [m["request"]["subtype"] for m in sent] == ["interrupt"]
    assert sent[0]["request_id"]


def test_interrupt_denies_the_pending_wait_first_with_the_interrupted_reason():
    """A pending request the child still holds would survive the interrupt."""
    daemon = AgentDaemon("/tmp/x.sock")
    agent, sent = _sending_agent()
    agent.state = replay("permission-request.jsonl", stop_before_control=True)
    daemon.agents["a1"] = agent
    asyncio.run(_handle(daemon, {"cmd": "interrupt", "id": "a1"}))
    assert sent[0]["type"] == "control_response"
    assert sent[0]["response"]["response"]["behavior"] == "deny"
    assert sent[0]["response"]["response"]["message"] == INTERRUPTED_REASON
    assert sent[1]["request"]["subtype"] == "interrupt"


def test_interrupt_refuses_an_exited_agent():

    daemon = AgentDaemon("/tmp/x.sock")
    agent, sent = _sending_agent()
    agent.state = mark_exited(agent.state, 1)
    daemon.agents["a1"] = agent
    reply = asyncio.run(_handle(daemon, {"cmd": "interrupt", "id": "a1"}))
    assert "has exited" in reply["error"]
    assert sent == []


def test_a_reply_the_daemon_writes_reaches_every_watcher():
    """An attached client must see a wait resolve, whoever resolved it."""
    daemon = AgentDaemon("/tmp/x.sock")
    agent, _ = _sending_agent()
    agent.state = replay("permission-request.jsonl", stop_before_control=True)
    daemon.agents["a1"] = agent
    writer = _recording_writer()

    async def attach_then_approve():
        task = asyncio.create_task(daemon._attach("a1", writer))
        await asyncio.sleep(0)
        await daemon.handle({"cmd": "approve", "id": "a1"})
        await asyncio.sleep(0)
        task.cancel()

    asyncio.run(attach_then_approve())
    assert json.loads(writer.lines[-1])["type"] == "control_response"


def test_approving_a_plan_puts_the_agent_into_auto():
    """An approved plan is one to carry out, not one to re-ask about."""
    daemon = AgentDaemon("/tmp/x.sock", specs=InMemoryAgentSpecStore())
    daemon.specs.write(AgentSpec(agent_id="a1", cwd="/tmp/x", session_id="s1"))
    agent, sent = _answering_agent()
    agent.state = replay("plan-review-with-plan.jsonl", stop_before_control=True)
    daemon.agents["a1"] = agent

    reply = asyncio.run(_handle(daemon, {"cmd": "approve", "id": "a1"}))

    # The reply names the mode, so a caller can see what the approval did.
    assert reply == {"ok": True, "mode": "auto"}
    # The allow goes first: the mode request must not overtake the reply the
    # child is still waiting on.
    assert sent[0]["response"]["response"]["behavior"] == "allow"
    assert sent[1]["request"] == {"subtype": "set_permission_mode", "mode": "auto"}


def test_approving_a_plan_records_auto_on_the_spawn_record():
    """A mode that misses the spawn record is reverted by the next daemon start."""
    daemon = AgentDaemon("/tmp/x.sock", specs=InMemoryAgentSpecStore())
    daemon.specs.write(AgentSpec(agent_id="a1", cwd="/tmp/x", session_id="s1"))
    agent, _ = _answering_agent()
    agent.state = replay("plan-review-with-plan.jsonl", stop_before_control=True)
    daemon.agents["a1"] = agent

    asyncio.run(_handle(daemon, {"cmd": "approve", "id": "a1"}))

    spec = daemon.specs.read("a1")
    assert spec is not None
    assert spec.permission_mode == "auto"


def test_a_refused_mode_does_not_undo_the_approval():
    """The allow already went out, so the plan is accepted either way."""
    daemon = AgentDaemon("/tmp/x.sock", specs=InMemoryAgentSpecStore())
    daemon.specs.write(AgentSpec(agent_id="a1", cwd="/tmp/x", session_id="s1"))
    agent, _ = _answering_agent(subtype="error")
    agent.state = replay("plan-review-with-plan.jsonl", stop_before_control=True)
    daemon.agents["a1"] = agent

    reply = asyncio.run(_handle(daemon, {"cmd": "approve", "id": "a1"}))

    assert reply["ok"] is True
    assert "bad mode" in reply["warning"]
    # Nothing may report the mode as changed when the child refused it.
    spec = daemon.specs.read("a1")
    assert spec is not None
    assert spec.permission_mode is None


def test_approving_an_ordinary_permission_leaves_the_mode_alone():
    """Only a plan review carries the operator's "go and do it"."""
    daemon = AgentDaemon("/tmp/x.sock", specs=InMemoryAgentSpecStore())
    daemon.specs.write(AgentSpec(agent_id="a1", cwd="/tmp/x", session_id="s1"))
    agent, sent = _answering_agent()
    agent.state = replay("permission-request.jsonl", stop_before_control=True)
    daemon.agents["a1"] = agent

    asyncio.run(_handle(daemon, {"cmd": "approve", "id": "a1"}))

    assert [m.get("request", {}).get("subtype") for m in sent] == [None]


def test_answering_clears_the_wait_at_once():
    """Without the echo the daemon's own state still advertises the wait."""
    daemon = AgentDaemon("/tmp/x.sock")
    agent, _ = _sending_agent()
    agent.state = replay("permission-request.jsonl", stop_before_control=True)
    daemon.agents["a1"] = agent
    asyncio.run(_handle(daemon, {"cmd": "approve", "id": "a1"}))

    assert agent.state.pending is None


# --- the detail frame: what the agent waits on, said on attach ---------------


def attach_frames(daemon: AgentDaemon, agent_id: str) -> list[dict]:
    """Attach, let the backlog flush, disconnect, and return what was written."""
    writer = _recording_writer()

    async def attach_then_disconnect():
        task = asyncio.create_task(daemon._attach(agent_id, writer))
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(attach_then_disconnect())
    return [json.loads(line) for line in writer.lines]


def test_attach_opens_with_the_agents_detail():
    """A client must know what the agent waits on without inferring it."""
    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    agent.state = replay("question-unanswered.jsonl", stop_before_control=True)
    daemon.agents["a1"] = agent
    first = attach_frames(daemon, "a1")[0]
    assert first["type"] == AGENT_DETAIL
    assert first["agent"]["waiting_kind"] == "awaiting-question"
    assert first["agent"]["questions"][0]["options"][0]["label"] == "Red"


def test_the_detail_frame_names_the_request_a_wait_can_be_answered_with():
    """A row alone can never make a wait answerable: it carries no request id."""
    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    agent.state = replay("permission-request.jsonl", stop_before_control=True)
    daemon.agents["a1"] = agent
    first = attach_frames(daemon, "a1")[0]
    assert first["agent"]["request_id"] == agent.state.pending.request_id


def test_the_detail_frame_comes_before_the_backlog():
    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    agent.state = mark_exited(replay("normal-turn.jsonl"), 0)
    daemon.agents["a1"] = agent
    writer = _recording_writer()
    asyncio.run(asyncio.wait_for(daemon._attach("a1", writer), timeout=2))
    kinds = [json.loads(line).get("type") for line in writer.lines]
    assert kinds[0] == AGENT_DETAIL
    assert kinds.index(AGENT_DETAIL) < kinds.index(BACKLOG_END)


def test_a_message_the_user_sends_is_not_recorded():
    """The child replays every user turn itself, marked ``isReplay``.


    Recording it here would put one turn on the stream twice, and the
    orchestrator's normaliser mints a fresh item id per copy — so the user's
    own message would render twice.
    """
    daemon = AgentDaemon("/tmp/x.sock")
    agent, sent = _sending_agent()
    daemon.agents["a1"] = agent
    writer = _recording_writer()

    async def attach_then_say():
        task = asyncio.create_task(daemon._attach("a1", writer))
        await asyncio.sleep(0)
        await daemon.handle({"cmd": "say", "id": "a1", "text": "carry on"})
        await asyncio.sleep(0)
        agent.record({**user_message("carry on"), "isReplay": True})
        await asyncio.sleep(0)
        task.cancel()

    asyncio.run(attach_then_say())
    assert sent[-1]["message"]["content"][0]["text"] == "carry on"
    # The daemon writes nothing of its own; the one copy is the child's echo.
    assert [json.loads(line).get("type") for line in writer.lines] == [
        AGENT_DETAIL,
        BACKLOG_END,
        "user",
    ]


def test_interrupt_refuses_an_idle_agent():
    """An idle agent has no turn to abandon, so ok would be a lie."""
    daemon = AgentDaemon("/tmp/x.sock")
    agent, sent = _sending_agent()
    daemon.agents["a1"] = agent
    reply = asyncio.run(_handle(daemon, {"cmd": "interrupt", "id": "a1"}))
    assert "not running a turn" in reply["error"]
    assert sent == []


def test_interrupt_accepts_a_waiting_agent():
    """A wait is a turn the agent has not finished, so it is interruptible."""
    daemon = AgentDaemon("/tmp/x.sock")
    agent, sent = _sending_agent()
    agent.state = replay("permission-request.jsonl", stop_before_control=True)
    daemon.agents["a1"] = agent
    assert asyncio.run(_handle(daemon, {"cmd": "interrupt", "id": "a1"})) == {
        "ok": True
    }
    assert sent


# --- the attach cursor -------------------------------------------------------


def _frames(writer: _RecordingWriter) -> list[dict]:
    return [json.loads(line) for line in writer.lines]


def _attach_briefly(daemon: AgentDaemon, agent_id: str, **cursor) -> list[dict]:
    """Attach with ``cursor``, let the backlog flush, disconnect, return the frames."""
    writer = _recording_writer()

    async def attach_then_disconnect():
        task = asyncio.create_task(daemon._attach(agent_id, writer, **cursor))
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(attach_then_disconnect())
    return _frames(writer)


def test_the_backlog_carries_a_seq_per_event_and_ends_with_the_epoch_and_seq():
    from maelstrom.agent_model import SEQ_KEY

    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    agent.state = replay("normal-turn.jsonl")
    daemon.agents["a1"] = agent
    frames = _attach_briefly(daemon, "a1")
    replayed = [f for f in frames if SEQ_KEY in f]
    assert [f[SEQ_KEY] for f in replayed] == list(range(1, len(replayed) + 1))
    assert frames[-1] == {
        "type": BACKLOG_END,
        "epoch": agent.epoch,
        "seq": agent.state.seq,
    }


def test_a_cursor_from_this_life_replays_only_what_came_after_it():
    from maelstrom.agent_model import SEQ_KEY

    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    agent.state = replay("normal-turn.jsonl")
    daemon.agents["a1"] = agent
    frames = _attach_briefly(daemon, "a1", from_seq=2, epoch=agent.epoch)
    seqs = [f[SEQ_KEY] for f in frames if SEQ_KEY in f]
    assert seqs == list(range(3, agent.state.seq + 1))
    assert TRUNCATED not in [f.get("type") for f in frames]


def test_a_cursor_from_another_life_is_ignored_and_the_whole_window_replays():
    from maelstrom.agent_model import SEQ_KEY

    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    agent.state = replay("normal-turn.jsonl")
    daemon.agents["a1"] = agent
    frames = _attach_briefly(daemon, "a1", from_seq=2, epoch="someone-else")
    seqs = [f[SEQ_KEY] for f in frames if SEQ_KEY in f]
    assert seqs[0] == 1


def test_a_cursor_the_ring_has_rolled_past_gets_a_truncated_marker_first(monkeypatch):
    """The client asked from seq 1; the ring starts later, so it is told how much is gone."""
    from maelstrom import agent_model
    from maelstrom.agent_model import SEQ_KEY

    monkeypatch.setattr(agent_model, "RECENT_LIMIT", 3)
    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    for _ in range(10):
        agent.record({"type": "rate_limit_event"})
    daemon.agents["a1"] = agent
    frames = _attach_briefly(daemon, "a1", from_seq=1, epoch=agent.epoch)
    kinds = [f.get("type") for f in frames]
    assert kinds[:2] == [AGENT_DETAIL, TRUNCATED]
    # Seqs 2..7 are gone: the ring holds 8, 9, 10.
    assert frames[1]["dropped"] == 6
    assert [f[SEQ_KEY] for f in frames if SEQ_KEY in f] == [8, 9, 10]


def test_a_fresh_attach_to_a_rolled_ring_says_how_many_are_gone(monkeypatch):
    from maelstrom import agent_model

    monkeypatch.setattr(agent_model, "RECENT_LIMIT", 3)
    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    for _ in range(5):
        agent.record({"type": "rate_limit_event"})
    daemon.agents["a1"] = agent
    frames = _attach_briefly(daemon, "a1")
    assert frames[1] == {"type": TRUNCATED, "dropped": 2}


def test_two_watchers_on_one_agent_both_receive_a_recorded_event():
    from maelstrom.agent_model import SEQ_KEY

    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    daemon.agents["a1"] = agent
    one, two = _recording_writer(), _recording_writer()

    async def scenario():
        tasks = [
            asyncio.create_task(daemon._attach("a1", one)),
            asyncio.create_task(daemon._attach("a1", two)),
        ]
        await asyncio.sleep(0)
        agent.record({"type": "assistant", "message": {"content": []}})
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        for task in tasks:
            task.cancel()

    asyncio.run(scenario())
    for writer in (one, two):
        last = _frames(writer)[-1]
        assert last["type"] == "assistant"
        assert last[SEQ_KEY] == 1


def test_a_watcher_that_falls_a_queue_behind_is_told_what_it_lost_once(monkeypatch):
    """The overflow used to drop the oldest silently. Now the seq jump is marked."""
    from maelstrom import agent_server
    from maelstrom.agent_model import SEQ_KEY

    monkeypatch.setattr(agent_server, "WATCHER_QUEUE_LIMIT", 2)
    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    daemon.agents["a1"] = agent
    writer = _recording_writer()

    async def scenario():
        task = asyncio.create_task(daemon._attach("a1", writer))
        await asyncio.sleep(0)
        # Five events land before the writer's loop runs again: the queue of
        # two keeps the last two.
        for _ in range(5):
            agent.record({"type": "rate_limit_event"})
        for _ in range(4):
            await asyncio.sleep(0)
        task.cancel()

    asyncio.run(scenario())
    frames = _frames(writer)
    live = [
        f
        for f in frames
        if f.get("type") != AGENT_DETAIL and f.get("type") != BACKLOG_END
    ]
    assert live[0] == {"type": TRUNCATED, "dropped": 3}
    seqs = [f[SEQ_KEY] for f in live if SEQ_KEY in f]
    assert seqs == [4, 5]
    assert len(seqs) == len(set(seqs))


# --- set-mode: the one command that reads the child's answer ---------------


def _answering_agent(subtype: str = "success", agent_id: str = "a1"):
    """A stub agent that answers every control request the daemon sends it.

    ``set-mode`` is the one command that waits for the child, so a stub that
    never replies would hang the test rather than fail it.
    """
    agent = _stub_agent(agent_id)
    sent: list[dict] = []

    async def record(message: dict) -> bool:
        sent.append(message)
        if message.get("type") == "control_request":
            agent.record(
                {
                    "type": "control_response",
                    "response": {
                        "subtype": subtype,
                        "request_id": message["request_id"],
                        **({"error": "bad mode"} if subtype == "error" else {}),
                    },
                }
            )
        return True

    agent.send = record  # type: ignore[method-assign]
    return agent, sent


def test_set_mode_sends_the_control_request_with_the_wire_word():
    daemon = AgentDaemon("/tmp/x.sock", specs=InMemoryAgentSpecStore())
    daemon.specs.write(AgentSpec(agent_id="a1", cwd="/tmp/x", session_id="s1"))
    agent, sent = _answering_agent()
    daemon.agents["a1"] = agent
    reply = asyncio.run(
        _handle(daemon, {"cmd": "set-mode", "id": "a1", "mode": "normal"})
    )
    assert reply == {"ok": True, "mode": "normal"}
    assert sent[0]["request"] == {"subtype": "set_permission_mode", "mode": "default"}


def test_set_mode_rewrites_the_spawn_record():
    """Without this a resume or a daemon restart silently reverts the mode."""
    daemon = AgentDaemon("/tmp/x.sock", specs=InMemoryAgentSpecStore())
    daemon.specs.write(AgentSpec(agent_id="a1", cwd="/tmp/x", session_id="s1"))
    daemon.agents["a1"] = _answering_agent()[0]
    asyncio.run(_handle(daemon, {"cmd": "set-mode", "id": "a1", "mode": "auto"}))
    spec = daemon.specs.read("a1")
    assert spec is not None
    assert spec.permission_mode == "auto"


def test_set_mode_refuses_an_unknown_mode_before_touching_the_child():
    daemon = AgentDaemon("/tmp/x.sock", specs=InMemoryAgentSpecStore())
    agent, sent = _answering_agent()
    daemon.agents["a1"] = agent
    reply = asyncio.run(
        _handle(daemon, {"cmd": "set-mode", "id": "a1", "mode": "nonsense"})
    )
    assert "unknown mode" in reply["error"]
    assert sent == []


def test_set_mode_reports_a_mode_the_child_refused():
    """An error reply must not be recorded as a change that happened."""
    daemon = AgentDaemon("/tmp/x.sock", specs=InMemoryAgentSpecStore())
    daemon.specs.write(AgentSpec(agent_id="a1", cwd="/tmp/x", session_id="s1"))
    daemon.agents["a1"] = _answering_agent(subtype="error")[0]
    reply = asyncio.run(
        _handle(daemon, {"cmd": "set-mode", "id": "a1", "mode": "plan"})
    )
    assert "error" in reply
    spec = daemon.specs.read("a1")
    assert spec is not None
    assert spec.permission_mode is None


def test_set_mode_reports_a_child_that_never_answers():
    """The timeout is what stops one quiet child holding the socket open."""
    daemon = AgentDaemon("/tmp/x.sock", specs=InMemoryAgentSpecStore())
    daemon.specs.write(AgentSpec(agent_id="a1", cwd="/tmp/x", session_id="s1"))
    agent, _ = _sending_agent()  # sends, but never answers
    daemon.agents["a1"] = agent
    with patch.object(agent_server, "REQUEST_TIMEOUT", 0.01):
        reply = asyncio.run(
            _handle(daemon, {"cmd": "set-mode", "id": "a1", "mode": "auto"})
        )
    assert "did not answer" in reply["error"]
    spec = daemon.specs.read("a1")
    assert spec is not None
    assert spec.permission_mode is None


def test_set_mode_reports_a_child_whose_stdin_would_not_take_it():
    daemon = AgentDaemon("/tmp/x.sock", specs=InMemoryAgentSpecStore())
    daemon.specs.write(AgentSpec(agent_id="a1", cwd="/tmp/x", session_id="s1"))
    agent = _stub_agent()  # a closing stdin, so `send` returns False

    async def refuse(message: dict) -> bool:
        return False

    agent.send = refuse  # type: ignore[method-assign]
    daemon.agents["a1"] = agent
    reply = asyncio.run(
        _handle(daemon, {"cmd": "set-mode", "id": "a1", "mode": "auto"})
    )
    assert "could not reach agent" in reply["error"]


def test_set_mode_reports_a_child_that_dies_mid_request():
    """The child's death ends the wait, and says so rather than cancelling us.

    A cancellation here would be indistinguishable from the daemon shutting
    down, so `pump` fails the waiter instead.
    """
    daemon = AgentDaemon("/tmp/x.sock", specs=InMemoryAgentSpecStore())
    daemon.specs.write(AgentSpec(agent_id="a1", cwd="/tmp/x", session_id="s1"))
    agent = _stub_agent()

    async def send_then_die(message: dict) -> bool:
        # The child takes the request, then its stream ends before it answers.
        agent.fail_waiters()
        return True

    agent.send = send_then_die  # type: ignore[method-assign]
    daemon.agents["a1"] = agent
    reply = asyncio.run(
        _handle(daemon, {"cmd": "set-mode", "id": "a1", "mode": "auto"})
    )
    assert "has exited" in reply["error"]
    spec = daemon.specs.read("a1")
    assert spec is not None
    assert spec.permission_mode is None


def test_set_mode_refuses_an_exited_agent():
    daemon = AgentDaemon("/tmp/x.sock", specs=InMemoryAgentSpecStore())
    agent, sent = _answering_agent()
    agent.state = mark_exited(agent.state, 1)
    daemon.agents["a1"] = agent
    reply = asyncio.run(
        _handle(daemon, {"cmd": "set-mode", "id": "a1", "mode": "auto"})
    )
    assert "has exited" in reply["error"]
    assert sent == []


def test_resume_brings_back_an_agent_that_was_stopped():
    """The point of keeping the record: a stop is undoable.

    A stopped agent is deliberately absent from ``self.agents``, so ``resume``
    has to reach the record store rather than the live agents.
    """
    daemon, specs = _daemon_with_specs()
    _spawning(daemon, [{"cmd": "start", "cwd": "/tmp/x", "model": "opus"}])
    agent_id = next(iter(daemon.agents))
    asyncio.run(_handle(daemon, {"cmd": "stop", "id": agent_id}))
    reply = _spawning(daemon, [{"cmd": "resume", "id": agent_id}], returning=True)
    assert reply == {"ok": True, "id": agent_id}
    # Back under its own id, and no longer marked stopped: the stub child ends
    # at once, so the record the pump leaves behind reads `exited`.
    assert agent_id in daemon.agents
    assert specs.read(agent_id).status != SPEC_STOPPED


def test_a_stopped_row_names_the_agent_id_that_resumes_it():
    """A listing whose whole purpose is a resume must print what resume takes."""
    daemon, _ = _stopped_daemon([_meta("sid-1", cwd="/tmp/x")], records=False)
    _spawning(daemon, [{"cmd": "start", "cwd": "/tmp/x", "session": "sid-1"}])
    agent_id = next(iter(daemon.agents))
    asyncio.run(_handle(daemon, {"cmd": "stop", "id": agent_id}))
    (row,) = asyncio.run(_handle(daemon, {"cmd": "list", "scope": "stopped"}))["agents"]
    assert row["id"] == agent_id
    assert row["session"] == "sid-1"


def test_a_transcript_with_no_record_is_not_listed():
    """A hand-started session cannot be resumed, so offering it only fails.

    ``_resume`` reads the model, permission mode and env from the record. A
    transcript alone gives the daemon nothing to spawn from, and the resume
    would come back ``no such agent``.
    """
    daemon, _ = _stopped_daemon([_meta("sid-1", cwd="/tmp/x")], records=False)
    rows = asyncio.run(_handle(daemon, {"cmd": "list", "scope": "stopped"}))["agents"]
    assert rows == []


def test_the_record_fallback_refuses_to_resume_a_record_still_running():
    """The in-memory path refuses a running agent; the fallback must too.

    A record left ``running`` by a daemon that is still alive would otherwise
    put a second child on one session id, which is the transcript contention
    ``resume`` exists to prevent.
    """
    daemon, specs = _daemon_with_specs()
    specs.write(
        AgentSpec(agent_id="a1", cwd="/tmp/x", session_id="s1", status="running")
    )
    reply = asyncio.run(_handle(daemon, {"cmd": "resume", "id": "a1"}))
    assert reply["error"] == "agent a1 is running"
    assert daemon.agents == {}


def test_a_listing_opens_the_task_index_once_not_once_per_session():
    """~800 transcripts must not mean ~800 SQLite connections.

    Each open runs `ensure_excludes()`, a `PRAGMA journal_mode=WAL` and a
    `CREATE TABLE IF NOT EXISTS` before its one-row SELECT, which is what
    `session_view` avoids by taking the index as a collaborator.
    """
    metas = [_meta(f"s{i}", cwd="/tmp/x") for i in range(20)]
    daemon, _ = _stopped_daemon(metas)
    rows = asyncio.run(_handle(daemon, {"cmd": "list", "scope": "stopped"}))["agents"]
    assert len(rows) == 20
    assert len(daemon._test_opens) == 1


def test_the_listing_prefers_a_stopped_record_over_an_exited_one():
    """A task relaunched under its own session id leaves a record per run.

    The task session id never changes, so every launch of one task writes
    another record against it. The listing keys its records by session id, so
    the several collapse to one. A ``stopped`` record wins, because a stop is
    deliberate and an ``exited`` one is a crash.

    The winner must come from the records themselves, not from the store: the
    two backends list in different orders. Written in both orders here, because
    a rule that holds for only one of them is the bug rather than the fix.
    """
    stopped = AgentSpec(
        agent_id="aaa",
        cwd="/tmp/x",
        session_id="s1",
        model="opus",
        status=SPEC_STOPPED,
    )
    exited = AgentSpec(
        agent_id="zzz",
        cwd="/tmp/x",
        session_id="s1",
        model="sonnet",
        status=SPEC_EXITED,
    )
    for order in ([stopped, exited], [exited, stopped]):
        daemon, specs = _stopped_daemon([_meta("s1", cwd="/tmp/x")], records=False)
        for spec in order:
            specs.write(spec)
        (row,) = asyncio.run(_handle(daemon, {"cmd": "list", "scope": "stopped"}))[
            "agents"
        ]
        assert row["id"] == "aaa"
        assert row["model"] == "opus"


# --- subagents on the socket ---------------------------------------------------


def _parented(parent_id: str, text: str) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
        "parent_tool_use_id": parent_id,
        "task_description": "a task",
    }


def _notification(tool_use_id: str, status: str = "completed") -> dict:
    return {
        "type": "system",
        "subtype": "task_notification",
        "task_id": "t",
        "tool_use_id": tool_use_id,
        "status": status,
        "summary": "done",
    }


def _agent_with_subagent() -> tuple[AgentDaemon, Agent]:
    """A daemon holding ``a1`` with a live subagent ``a1.1`` that said two things."""
    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    agent.record({"type": "assistant", "message": {"content": []}})
    agent.record(_parented("t1", "one"))
    agent.record(_parented("t1", "two"))
    daemon.agents["a1"] = agent
    return daemon, agent


def test_list_names_each_subagent_under_its_parent():
    """Driven by a recorded stream, so the row reads the shapes claude sends."""
    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    agent.state = replay("subagent-turn.jsonl")
    daemon.agents["a1"] = agent
    reply = asyncio.run(_handle(daemon, {"cmd": "list"}))
    assert [(r["id"], r["parent"], r["state"]) for r in reply["agents"]] == [
        ("a1", "", "idle"),
        ("a1.1", "a1", "exited(0)"),
    ]
    assert reply["agents"][1]["description"] == "List and summarise docs/dev"


def test_show_on_a_subagent_prints_the_subagent():
    daemon, _ = _agent_with_subagent()
    reply = asyncio.run(_handle(daemon, {"cmd": "show", "id": "a1.1"}))
    assert reply["agent"]["id"] == "a1.1"
    assert reply["agent"]["message"] == "two"


def test_show_on_an_unopened_subagent_is_no_such_agent():
    daemon, _ = _agent_with_subagent()
    reply = asyncio.run(_handle(daemon, {"cmd": "show", "id": "a1.9"}))
    assert "no such agent" in reply["error"]


def test_driving_a_subagent_is_refused_with_the_parent_named():
    daemon, _ = _agent_with_subagent()
    for command in ("say", "approve", "deny", "answer", "interrupt", "stop", "resume"):
        reply = asyncio.run(
            _handle(daemon, {"cmd": command, "id": "a1.1", "text": "x"})
        )
        assert reply["error"] == "a1.1 is a subagent of a1; drive a1", command
    reply = asyncio.run(
        _handle(daemon, {"cmd": "set-mode", "id": "a1.1", "mode": "auto"})
    )
    assert reply["error"] == "a1.1 is a subagent of a1; drive a1"


def test_a_typo_against_a_subagent_is_an_unknown_command_not_a_refusal():
    daemon, _ = _agent_with_subagent()
    reply = asyncio.run(_handle(daemon, {"cmd": "wat", "id": "a1.1"}))
    assert "unknown command" in reply["error"]


def test_a_parent_attach_never_sees_a_subagents_event():
    from maelstrom.agent_model import SEQ_KEY

    daemon, agent = _agent_with_subagent()
    writer = _recording_writer()

    async def scenario():
        task = asyncio.create_task(daemon._attach("a1", writer))
        await asyncio.sleep(0)
        agent.record(_parented("t1", "three"))
        agent.record({"type": "assistant", "message": {"content": []}})
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()

    asyncio.run(scenario())
    frames = _frames(writer)
    assert all(f.get("parent_tool_use_id") is None for f in frames)
    live = [f for f in frames if SEQ_KEY in f]
    assert [f[SEQ_KEY] for f in live] == [1, 2]


def test_attach_to_a_subagent_opens_with_its_detail_and_replays_its_ring():
    from maelstrom.agent_model import SEQ_KEY

    daemon, agent = _agent_with_subagent()
    frames = _attach_briefly(daemon, "a1.1")
    assert frames[0]["type"] == AGENT_DETAIL
    assert frames[0]["agent"]["id"] == "a1.1"
    assert frames[0]["agent"]["parent"] == "a1"
    replayed = [f for f in frames if SEQ_KEY in f]
    assert [f[SEQ_KEY] for f in replayed] == [1, 2]
    assert all(f["parent_tool_use_id"] == "t1" for f in replayed)
    assert frames[-1] == {"type": BACKLOG_END, "epoch": agent.epoch, "seq": 2}


def test_a_cursor_on_a_subagent_replays_only_what_is_newer():
    from maelstrom.agent_model import SEQ_KEY

    daemon, agent = _agent_with_subagent()
    frames = _attach_briefly(daemon, "a1.1", from_seq=1, epoch=agent.epoch)
    assert [f[SEQ_KEY] for f in frames if SEQ_KEY in f] == [2]
    assert TRUNCATED not in [f.get("type") for f in frames]


def test_a_subagent_attach_follows_its_live_events():
    from maelstrom.agent_model import SEQ_KEY

    daemon, agent = _agent_with_subagent()
    writer = _recording_writer()

    async def scenario():
        task = asyncio.create_task(daemon._attach("a1.1", writer))
        await asyncio.sleep(0)
        agent.record({"type": "assistant", "message": {"content": []}})
        agent.record(_parented("t1", "three"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()

    asyncio.run(scenario())
    live = [f for f in _frames(writer) if SEQ_KEY in f]
    assert [f[SEQ_KEY] for f in live] == [1, 2, 3]
    assert live[-1]["message"]["content"][0]["text"] == "three"


def test_the_notification_ends_the_subagents_stream_and_only_that_one():
    from maelstrom.agent_model import AGENT_EXITED

    daemon, agent = _agent_with_subagent()
    parent, child = _recording_writer(), _recording_writer()

    async def scenario():
        parent_task = asyncio.create_task(daemon._attach("a1", parent))
        child_task = asyncio.create_task(daemon._attach("a1.1", child))
        await asyncio.sleep(0)
        agent.record(_notification("t1"))
        await asyncio.wait_for(child_task, timeout=2)
        await asyncio.sleep(0)
        parent_task.cancel()

    asyncio.run(scenario())
    assert _frames(child)[-1] == {"type": AGENT_EXITED, "exit_code": 0}
    kinds = [f.get("type") for f in _frames(parent)]
    assert AGENT_EXITED not in kinds
    assert "system" in kinds  # the notification itself is the parent's


def test_a_failed_subagent_ends_its_stream_with_exit_one():
    from maelstrom.agent_model import AGENT_EXITED

    daemon, agent = _agent_with_subagent()
    child = _recording_writer()

    async def scenario():
        task = asyncio.create_task(daemon._attach("a1.1", child))
        await asyncio.sleep(0)
        agent.record(_notification("t1", "failed"))
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())
    assert _frames(child)[-1] == {"type": AGENT_EXITED, "exit_code": 1}


def test_attaching_to_an_ended_subagent_ends_after_the_backlog():
    from maelstrom.agent_model import AGENT_EXITED

    daemon, agent = _agent_with_subagent()
    agent.record(_notification("t1"))
    writer = _recording_writer()
    asyncio.run(asyncio.wait_for(daemon._attach("a1.1", writer), timeout=2))
    kinds = [f.get("type") for f in _frames(writer)]
    assert kinds[-2:] == [BACKLOG_END, AGENT_EXITED]


def test_a_parent_exit_reaches_the_subagents_watchers_too():
    from maelstrom.agent_model import AGENT_EXITED

    proc = MagicMock()
    proc.stdin.is_closing.return_value = True
    proc.stdout.readline = AsyncMock(return_value=b"")
    proc.wait = AsyncMock(return_value=3)
    daemon = AgentDaemon("/tmp/x.sock")
    agent = Agent("a1", "/tmp/x", proc)
    agent.record(_parented("t1", "one"))
    daemon.agents["a1"] = agent
    parent, child = _recording_writer(), _recording_writer()

    async def scenario():
        tasks = [
            asyncio.create_task(daemon._attach("a1", parent)),
            asyncio.create_task(daemon._attach("a1.1", child)),
        ]
        await asyncio.sleep(0)
        await agent.pump()
        for task in tasks:
            await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())
    assert _frames(parent)[-1] == {"type": AGENT_EXITED, "exit_code": 3}
    assert _frames(child)[-1] == {"type": AGENT_EXITED, "exit_code": 3}


def test_attach_to_an_unopened_subagent_is_no_such_agent():
    daemon, _ = _agent_with_subagent()
    writer = _recording_writer()
    asyncio.run(daemon._attach("a1.9", writer))
    assert "no such agent" in _frames(writer)[0]["error"]
