"""`mael agent` commands, driven through the recording transport."""

import asyncio
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
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


def run_cli(argv: list[str], replies: list[dict] | None = None, resolve=None):
    """Drive `mael agent` through the fake transport, and return (result, client).

    ``resolve`` stands in for ``resolve_context``, which reads the real config
    and walks the real filesystem.
    """
    client = RecordingDaemonClient(replies=list(replies or []))
    agent_cli._client_factory = lambda: client
    original = agent_cli.resolve_context
    if resolve is not None:
        agent_cli.resolve_context = resolve
    try:
        return CliRunner().invoke(agent_cli.agent, argv), client
    finally:
        agent_cli._client_factory = SocketDaemonClient
        agent_cli.resolve_context = original


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


def test_interrupt_sends_the_interrupt_command():
    _, client = run_cli(["interrupt", "a1"])
    assert client.calls == [{"cmd": "interrupt", "id": "a1"}]


def test_set_mode_sends_the_mode():
    _, client = run_cli(["set-mode", "a1", "auto"], [{"ok": True, "mode": "auto"}])
    assert client.calls == [{"cmd": "set-mode", "id": "a1", "mode": "auto"}]


def test_set_mode_refuses_a_mode_that_is_not_one_of_the_three():
    """Click rejects it, so no daemon round trip is spent on a typo."""
    result, client = run_cli(["set-mode", "a1", "nonsense"])
    assert result.exit_code != 0
    assert client.calls == []


def test_attach_refuses_without_a_terminal_and_names_tail():
    """The TUI needs a terminal, and the read-only view is what works without one."""
    result, client = run_cli(["attach", "a1"])
    assert result.exit_code != 0
    assert "mael agent tail -f a1" in result.output
    assert client.calls == []


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
    assert "mode" in result.output
    assert "normal" in result.output


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


def test_tail_follow_ends_when_the_agent_has_exited():
    """``-f`` on a dead agent must return, because the stream says it ended."""
    from maelstrom.agent_model import mark_exited

    with _serving(mark_exited(replay("normal-turn.jsonl"), 0)):
        result = CliRunner().invoke(agent_cli.agent, ["tail", "-f", "a1"])
    assert result.exit_code == 0
    assert "Hello there, friend" in result.output


def test_resume_sends_the_agent_id():
    result, client = run_cli(["resume", "a1"], [{"ok": True, "id": "a1"}])
    assert result.exit_code == 0
    assert client.calls == [{"cmd": "resume", "id": "a1", "text": ""}]


def test_resume_passes_the_text_the_user_gave():
    _, client = run_cli(["resume", "a1", "--text", "carry on"], [{"ok": True}])
    assert client.calls[0]["text"] == "carry on"


def test_resume_of_a_running_agent_exits_non_zero():
    result, _ = run_cli(["resume", "a1"], [{"error": "agent a1 is running"}])
    assert result.exit_code == 1
    assert "is running" in result.output


def test_tail_says_how_many_earlier_events_the_daemon_dropped(monkeypatch):
    """A ring that rolled is reported as a line, not as a silent hole in history."""
    from maelstrom import agent_model

    monkeypatch.setattr(agent_model, "RECENT_LIMIT", 3)
    state = replay("normal-turn.jsonl")
    with _serving(state):
        result = CliRunner().invoke(agent_cli.agent, ["tail", "a1"])
    assert result.exit_code == 0
    assert f"— {state.seq - 3} earlier events dropped" in result.output
    assert "Hello there, friend" in result.output


# --- the stopped listing ----------------------------------------------------


def stopped_row(**kw) -> dict:
    row = {
        "id": "s1",
        "kind": "cli",
        "age": "2h",
        "task": "2026-09-04.2",
        "branch": "feat/x",
        "label": "Improve plan mode",
        "cwd": "/w/alpha",
        "model": "",
        "mode": "",
        "modified_at": 1.0,
    }
    row.update(kw)
    return row


def test_stopped_asks_the_daemon_for_the_stopped_scope():
    _, client = run_cli(["list", "--stopped"], [{"agents": []}])
    assert client.calls[0]["scope"] == "stopped"


def test_all_asks_the_daemon_for_both_scopes():
    _, client = run_cli(["list", "--all"], [{"agents": []}])
    assert client.calls[0]["scope"] == "all"


def test_a_plain_list_sends_no_scope_so_an_old_daemon_still_answers():
    _, client = run_cli(["list"], [{"agents": []}])
    assert "scope" not in client.calls[0]


def test_stopped_and_all_together_are_refused():
    result, _ = run_cli(["list", "--stopped", "--all"], [{"agents": []}])
    assert result.exit_code != 0


def test_the_stopped_listing_renders_the_session_id_and_its_label():
    """The id is what ``mael agent resume`` needs, and nothing else prints it."""
    result, _ = run_cli(["list", "--stopped"], [{"agents": [stopped_row()]}])
    assert result.exit_code == 0
    assert "s1" in result.output
    assert "Improve plan mode" in result.output
    assert "2026-09-04.2" in result.output


def test_an_empty_stopped_listing_says_so_in_a_sentence():
    result, _ = run_cli(["list", "--stopped"], [{"agents": []}])
    assert "No stopped sessions." in result.output


def test_a_worktree_filter_reaches_the_daemon_as_a_path(monkeypatch, tmp_path):
    """The CLI knows what a project is; the daemon only ever sees a cwd."""
    worktree = tmp_path / "maelstrom" / "alpha"
    worktree.mkdir(parents=True)
    monkeypatch.setattr(
        agent_cli,
        "resolve_context",
        lambda *a, **kw: SimpleNamespace(worktree_path=worktree, project_path=None),
    )
    _, client = run_cli(
        ["list", "--stopped", "-w", "maelstrom.alpha"], [{"agents": []}]
    )
    assert client.calls[0]["cwd"] == str(worktree)


def test_a_project_filter_reaches_the_daemon_as_the_project_path(monkeypatch, tmp_path):
    project = tmp_path / "maelstrom"
    project.mkdir(parents=True)
    monkeypatch.setattr(
        agent_cli,
        "resolve_context",
        lambda *a, **kw: SimpleNamespace(worktree_path=None, project_path=project),
    )
    _, client = run_cli(
        ["list", "--stopped", "--project", "maelstrom"], [{"agents": []}]
    )
    assert client.calls[0]["cwd"] == str(project)


def test_an_unresolvable_worktree_fails_with_a_message_not_a_traceback():
    def boom(*a, **kw):
        raise ValueError("no such worktree: nope")

    result, _ = run_cli(
        ["list", "--stopped", "-w", "nope"], [{"agents": []}], resolve=boom
    )
    assert result.exit_code != 0
    assert "no such worktree" in result.output


def test_a_filter_without_a_scope_still_asks_for_the_stopped_one(monkeypatch, tmp_path):
    """``-w`` on its own is only meaningful against sessions that have stopped."""
    worktree = tmp_path / "alpha"
    worktree.mkdir()
    monkeypatch.setattr(
        agent_cli,
        "resolve_context",
        lambda *a, **kw: SimpleNamespace(worktree_path=worktree, project_path=None),
    )
    _, client = run_cli(["list", "-w", "maelstrom.alpha"], [{"agents": []}])
    assert client.calls[0]["scope"] == "stopped"


def test_the_all_listing_renders_both_kinds_of_row_legibly():
    """``--all`` mixes two row shapes, and neither may render as blank cells.

    A stopped row carries no ``state`` or ``cost``, and a running row carries no
    ``age``, ``task`` or ``label``. Rendering both through one column set drops
    whichever half the columns do not name.
    """
    running = build_agent_row(replay("normal-turn.jsonl"))
    result, _ = run_cli(["list", "--all"], [{"agents": [running, stopped_row()]}])
    assert result.exit_code == 0
    # The running row keeps what makes it a running row.
    assert "Hello there, friend" in result.output
    # ...and the stopped row keeps what makes it resumable.
    assert "Improve plan mode" in result.output
    assert "2026-09-04.2" in result.output


def test_an_empty_all_listing_names_both_things_it_looked_for():
    """``--all`` found neither, so naming only one of them would mislead."""
    result, _ = run_cli(["list", "--all"], [{"agents": []}])
    assert "No agents running or stopped." in result.output
