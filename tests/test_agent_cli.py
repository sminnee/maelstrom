"""`mael agent` commands, driven through the recording transport."""

import json
from pathlib import Path

from click.testing import CliRunner

from maelstrom import agent_cli
from maelstrom.agent_model import apply_event, build_agent_row
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
