"""`mael agent` commands, driven through the recording transport."""

import asyncio
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

from click.testing import CliRunner

from maelstrom import agent_cli
from maelstrom.agent_model import apply_event, build_agent_detail, build_agent_row
from maelstrom.agent_server import Agent, AgentDaemon
from maelstrom.agent_transport import RecordingDaemonClient, SocketDaemonClient

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


def run_cli(argv: list[str], replies: list[dict] | None = None):
    """Drive `mael agent` through the fake transport, and return (result, client)."""
    client = RecordingDaemonClient(replies=list(replies or []))
    agent_cli._client_factory = lambda: client
    try:
        return CliRunner().invoke(agent_cli.agent, argv), client
    finally:
        agent_cli._client_factory = SocketDaemonClient


def test_start_sends_the_cwd_and_the_prompt():
    result, client = run_cli(
        ["start", ".", "--prompt", "go", "--mode", "auto"], [{"id": "a1"}]
    )
    assert result.exit_code == 0
    assert result.output.strip() == "a1"
    sent = client.calls[0]
    assert sent["cmd"] == "start"
    assert sent["prompt"] == "go"
    assert sent["mode"] == "auto"
    assert Path(sent["cwd"]).is_absolute()


def test_start_forwards_a_session_id():
    """A pinned session id is what makes a driven agent resumable."""
    _, client = run_cli(["start", ".", "--session-id", "dead-beef"], [{"id": "a1"}])
    assert client.calls[0]["session"] == "dead-beef"


def test_answer_sends_the_choice():
    _, client = run_cli(["answer", "a1", "Green"])
    assert client.calls == [{"cmd": "answer", "id": "a1", "choice": "Green"}]


def test_deny_sends_the_reason():
    _, client = run_cli(["deny", "a1", "--reason", "not now"])
    assert client.calls[0]["reason"] == "not now"


def test_list_renders_the_wait_kind():
    rows = [
        build_agent_row(replay("question-unanswered.jsonl", stop_before_control=True))
    ]
    result, _ = run_cli(["list"], [{"agents": rows}])
    assert "awaiting-question" in result.output
    assert "Which colour do you prefer?" in result.output


def test_list_says_so_when_nothing_runs():
    result, _ = run_cli(["list"], [{"agents": []}])
    assert "No agents running." in result.output


def test_a_daemon_error_exits_non_zero():
    """The CLI must fail loudly, not print an error and report success."""
    result, _ = run_cli(["say", "a1", "hi"], [{"error": "agent a1 has exited"}])
    assert result.exit_code == 1
    assert "has exited" in result.output


def test_list_shows_what_the_agent_last_said():
    """Two working agents must not look identical."""
    rows = [build_agent_row(replay("normal-turn.jsonl"))]
    result, _ = run_cli(["list"], [{"agents": rows}])
    assert "Hello there, friend" in result.output


def test_show_prints_every_option_with_its_description():
    detail = build_agent_detail(
        replay("question-unanswered.jsonl", stop_before_control=True)
    )
    result, _ = run_cli(["show", "a1"], [{"agent": detail}])
    assert result.exit_code == 0
    assert "Which colour do you prefer?" in result.output
    assert "Green" in result.output
    assert "Natural, calm, fresh." in result.output


def test_show_ends_with_the_command_that_answers_the_wait():
    """Discoverability is the payoff: the next command is on screen."""
    detail = build_agent_detail(
        replay("question-unanswered.jsonl", stop_before_control=True)
    )
    result, _ = run_cli(["show", "a1"], [{"agent": detail}])
    assert "mael agent answer a1 Red" in result.output


def test_show_quotes_an_option_a_shell_would_otherwise_read():
    """An option label is model-written text, and the hint is made to be pasted."""
    detail = build_agent_detail(
        replay("question-unanswered.jsonl", stop_before_control=True)
    )
    detail["questions"][0]["options"][0]["label"] = 'Say "$(whoami)" now'
    result, _ = run_cli(["show", "a1"], [{"agent": detail}])
    assert "answer a1 'Say \"$(whoami)\" now'" in result.output


def test_show_names_approve_for_a_plan_review():
    detail = build_agent_detail(replay("plan-review.jsonl", stop_before_control=True))
    result, _ = run_cli(["show", "a1"], [{"agent": detail}])
    assert "mael agent approve a1" in result.output


def test_show_prints_the_plan_in_full():
    detail = build_agent_detail(
        replay("plan-review-with-plan.jsonl", stop_before_control=True)
    )
    result, _ = run_cli(["show", "a1"], [{"agent": detail}])
    assert "## Verification" in result.output


def test_show_names_the_file_the_plan_was_written_to():
    detail = build_agent_detail(
        replay("plan-review-with-plan.jsonl", stop_before_control=True)
    )
    result, _ = run_cli(["show", "a1"], [{"agent": detail}])
    assert "Plan file: " in result.output
    assert ".md" in result.output


def test_show_json_emits_the_detail_as_is():
    detail = build_agent_detail(replay("normal-turn.jsonl"))
    result, _ = run_cli(["show", "a1", "--json"], [{"agent": detail}])
    assert json.loads(result.output) == detail


def test_show_sends_the_show_command():
    detail = build_agent_detail(replay("normal-turn.jsonl"))
    _, client = run_cli(["show", "a1"], [{"agent": detail}])
    assert client.calls == [{"cmd": "show", "id": "a1"}]


# --- tail ------------------------------------------------------------------
#
# `tail` opens its own connection rather than going through `_client_factory`,
# so the only honest test is against a real daemon on a temp socket. The daemon
# runs on its own loop in a background thread, so the CLI's `asyncio.run` has
# the main thread to itself.


@contextmanager
def _serving(state, spy: list[dict] | None = None):
    """Serve one agent in ``state`` on a temp socket, for the body's duration."""
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
        socket_path = str(Path(tmp) / "d.sock")
        daemon = AgentDaemon(socket_path)
        proc = MagicMock()
        proc.stdin.is_closing.return_value = True
        agent = Agent("a1", "/tmp/x", proc)
        agent.state = state
        daemon.agents["a1"] = agent
        if spy is not None:
            original = daemon.handle

            async def record(payload: dict) -> dict:
                spy.append(payload)
                return await original(payload)

            daemon.handle = record  # type: ignore[method-assign]

        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def serve() -> None:
            asyncio.set_event_loop(loop)
            server = loop.run_until_complete(
                asyncio.start_unix_server(daemon._on_client, socket_path)
            )
            ready.set()
            loop.run_forever()
            # Let the handler tasks unwind before the loop closes, or their
            # writers raise "Event loop is closed" out of the reaper.
            server.close()
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        ready.wait(timeout=5)
        os.environ["MAEL_AGENT_SOCKET"] = socket_path
        try:
            yield
        finally:
            os.environ.pop("MAEL_AGENT_SOCKET", None)
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)


def test_tail_prints_the_history_and_exits():
    """Without ``-f`` it must stop at the backlog marker, not wait forever."""
    with _serving(replay("normal-turn.jsonl")):
        result = CliRunner().invoke(agent_cli.agent, ["tail", "a1"])
    assert result.exit_code == 0
    assert "Hello there, friend" in result.output


def test_tail_ignores_what_you_type():
    """``tail`` is read-only: typed input reaches no agent."""
    seen: list[dict] = []
    with _serving(replay("normal-turn.jsonl"), spy=seen):
        CliRunner().invoke(agent_cli.agent, ["tail", "a1"], input="hello agent\n")
    assert [payload.get("cmd") for payload in seen] == []


def test_tail_reports_an_unknown_agent():
    with _serving(replay("normal-turn.jsonl")):
        result = CliRunner().invoke(agent_cli.agent, ["tail", "nope"])
    assert "no such agent" in result.output
