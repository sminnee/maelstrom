"""The daemon's command surface, driven with a stub child instead of a subprocess."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from maelstrom import agent_server
from maelstrom.agent_model import (
    BACKLOG_END,
    DEFAULT_RESUME_PROMPT,
    EXITED,
    INTERRUPTED_REASON,
    PROCESSING,
    AgentSpec,
    apply_event,
    mark_exited,
)
from maelstrom.agent_server import Agent, AgentDaemon
from maelstrom.agent_spec_store import InMemoryAgentSpecStore

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
    assert reply["agent"]["messages"] == ["Hello there, friend"]


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
    assert [json.loads(line).get("type") for line in writer.lines] == [BACKLOG_END]


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

    async def record(message: dict) -> None:
        sent.append(message)

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


def _spawning(daemon, payloads, *, proc=None):
    """Run ``payloads`` through ``handle`` with a stubbed spawn; return the mock."""
    child = proc if proc is not None else _spawn_stub()

    async def run():
        for payload in payloads:
            await daemon.handle(payload)
        # The pump task marks the agent exited; let it finish before asserting.
        await asyncio.gather(
            *(a.pump_task for a in daemon.agents.values() if a.pump_task),
            return_exceptions=True,
        )

    with patch.object(
        agent_server.asyncio, "create_subprocess_exec", AsyncMock(return_value=child)
    ) as spawn:
        asyncio.run(run())
    return spawn


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


def test_stop_deletes_the_record_so_it_is_not_resumed():
    """A deliberate stop is not a crash; the next daemon must leave it alone."""
    daemon, specs = _daemon_with_specs()
    _spawning(daemon, [{"cmd": "start", "cwd": "/tmp/x"}])
    agent_id = next(iter(daemon.agents))
    asyncio.run(_handle(daemon, {"cmd": "stop", "id": agent_id}))
    assert specs.list() == []


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

    async def record(message: dict) -> None:
        sent.append(message)

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


def test_answering_clears_the_wait_at_once():
    """Without the echo the daemon's own state still advertises the wait."""
    daemon = AgentDaemon("/tmp/x.sock")
    agent, _ = _sending_agent()
    agent.state = replay("permission-request.jsonl", stop_before_control=True)
    daemon.agents["a1"] = agent
    asyncio.run(_handle(daemon, {"cmd": "approve", "id": "a1"}))
    assert agent.state.pending is None


def test_a_message_the_user_sends_reaches_every_watcher():
    """The child does not echo a user turn either, so `say` must record too."""
    daemon = AgentDaemon("/tmp/x.sock")
    agent, _ = _sending_agent()
    daemon.agents["a1"] = agent
    writer = _recording_writer()

    async def attach_then_say():
        task = asyncio.create_task(daemon._attach("a1", writer))
        await asyncio.sleep(0)
        await daemon.handle({"cmd": "say", "id": "a1", "text": "carry on"})
        await asyncio.sleep(0)
        task.cancel()

    asyncio.run(attach_then_say())
    last = json.loads(writer.lines[-1])
    assert last["type"] == "user"
    assert last["message"]["content"][0]["text"] == "carry on"


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
