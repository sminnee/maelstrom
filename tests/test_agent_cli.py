"""`mael agent` commands, driven through the recording transport."""

import asyncio
import json
import socket
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from maelstrom import admin_cli, agent_cli, agent_transport
from maelstrom.agent_model import (
    apply_event,
    build_agent_detail,
    build_agent_row,
    build_subagent_detail,
    build_subagent_rows,
)
from maelstrom.agent_server import Agent, AgentDaemon
from maelstrom.agent_stop import stop_agents_in_worktree
from maelstrom.agent_store import SqliteAgentStore, SqliteMilestoneStore
from maelstrom.agent_transport import RecordingDaemonClient, SocketAsyncDaemonClient
from maelstrom.agent_wire import AGENT_EXITED
from maelstrom.notebook_root import NOTEBOOK_ROOT_UNSET_MESSAGE, NotebookRootUnset
from maelstrom.state_db.migrate import open_state_db

from .agent_cli_support import drive, unreachable


class _TaskTable:
    """A task table that finds ``tasks[session_id]`` and counts its opens."""

    def __init__(self, tasks: dict[str, str]):
        self._tasks = tasks

    async def find_by_session_id(self, session_id: str):
        task_id = self._tasks.get(session_id)
        return SimpleNamespace(id=task_id) if task_id else None


@pytest.fixture(autouse=True)
def task_table(monkeypatch):
    """The task table every listing joins against, with one task on ``sess-1``.

    Autouse: a stopped listing opens the real notebook otherwise.
    """
    opens = []

    def open_table():
        opens.append(1)
        return _TaskTable({"sess-1": "2026-09-04.2"})

    monkeypatch.setattr(agent_cli, "open_task_table", open_table)
    return opens


@pytest.fixture(autouse=True)
def prompt_file(monkeypatch, tmp_path):
    """The system prompt file every start and resume names.

    Autouse: the real one lives in this checkout's ``shared/``, and a payload
    assertion should not depend on where the tests run.
    """
    path = tmp_path / "agent-prompt.md"
    monkeypatch.setattr(agent_cli, "agent_prompt_file", lambda: path)
    return path


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
    original = agent_cli.resolve_context
    if resolve is not None:
        agent_cli.resolve_context = resolve
    try:
        return drive(agent_cli.agent, argv, replies)
    finally:
        agent_cli.resolve_context = original


def test_a_warning_is_printed_without_failing_the_command():
    """A plan approval whose mode change the child refused still approved the plan.

    The command succeeded, so it must not exit non-zero -- but the refusal
    leaves the agent in the mode it was launched under, which the operator has
    to know about.
    """
    result, _ = run_cli(
        ["approve", "a1"], [{"ok": True, "warning": "agent a1 refused auto: bad mode"}]
    )
    assert result.exit_code == 0
    assert "refused auto" in result.output


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


def test_start_names_the_system_prompt_file(prompt_file):
    _, client = run_cli(["start", "."], [{"id": "a1"}])
    assert client.calls[0]["system_prompt_file"] == str(prompt_file)


def test_no_prompt_file_leaves_the_key_off(monkeypatch):
    """A tree that lost the file still launches; the daemon omits the flag."""
    monkeypatch.setattr(agent_cli, "agent_prompt_file", lambda: None)
    _, client = run_cli(["start", "."], [{"id": "a1"}])
    assert "system_prompt_file" not in client.calls[0]


def test_start_forwards_a_session_id():
    """A pinned session id is what makes a driven agent resumable."""
    _, client = run_cli(["start", ".", "--session-id", "dead-beef"], [{"id": "a1"}])
    assert client.calls[0]["session"] == "dead-beef"


def test_answer_sends_the_choice():
    _, client = run_cli(["answer", "a1", "Green"])
    assert client.calls == [
        {"cmd": "answer", "id": "a1", "choice": "Green", "request": ""}
    ]


def test_interrupt_sends_the_interrupt_command():
    _, client = run_cli(["interrupt", "a1"])
    assert client.calls == [{"cmd": "interrupt", "id": "a1"}]


def test_recover_sends_the_recover_command():
    _, client = run_cli(["recover", "a1"], [{"ok": True, "cleared": True}])
    assert client.calls == [{"cmd": "recover", "id": "a1"}]


def test_set_mode_sends_the_mode():
    _, client = run_cli(["set-mode", "a1", "auto"], [{"ok": True, "mode": "auto"}])
    assert client.calls == [{"cmd": "set-mode", "id": "a1", "mode": "auto"}]


def test_set_mode_refuses_a_mode_that_is_not_one_of_the_three():
    """Click rejects it, so no daemon round trip is spent on a typo."""
    result, client = run_cli(["set-mode", "a1", "nonsense"])
    assert result.exit_code != 0
    assert client.calls == []


def test_register_adopts_a_live_agent_with_no_record(tmp_path, monkeypatch):
    """A daemon-only agent, born before this branch, gets a store record."""
    monkeypatch.setenv("MAEL_NOTEBOOK_ROOT", str(tmp_path))
    assert CliRunner().invoke(admin_cli.cmd_migrate, []).exit_code == 0

    result, client = run_cli(
        ["register", "a1", "--task-id", "2026-09-16.4.3"],
        [
            {
                "agents": [
                    {
                        "id": "a1",
                        "session": "task-session-1",
                        "cwd": "/worktree",
                        "model": "claude:opus",
                        "mode": "plan",
                    }
                ]
            }
        ],
    )

    assert result.exit_code == 0, result.output
    assert client.calls == [{"cmd": "list"}]
    db = open_state_db(tmp_path / "state.db")
    try:
        stored = asyncio.run(SqliteAgentStore(db).list())
    finally:
        db.close()
    [record] = stored
    # A registered agent must be indistinguishable from a launched one: the
    # record carries a status and a start, or the router's sweep reads it as a
    # legacy row and cannot tell a new agent from an ancient one.
    assert record["started_at"]
    assert record | {"started_at": ""} == {
        "id": "a1",
        "harness": "claude",
        "task_session_id": "task-session-1",
        "task_id": "2026-09-16.4.3",
        "cwd": "/worktree",
        "model": "claude:opus",
        "mode": "plan",
        "status": "running",
        "started_at": "",
        "ended_at": "",
    }


def test_register_refuses_an_agent_id_the_daemon_does_not_list(tmp_path, monkeypatch):
    monkeypatch.setenv("MAEL_NOTEBOOK_ROOT", str(tmp_path))
    assert CliRunner().invoke(admin_cli.cmd_migrate, []).exit_code == 0

    result, client = run_cli(["register", "ghost"], [{"agents": []}])

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert client.calls == [{"cmd": "list"}]


def test_register_reports_an_unreachable_daemon():
    result, client = run_cli(["register", "a1"], [unreachable("/x")])
    assert result.exit_code == 1
    assert "Error:" in result.output


def test_attach_refuses_without_a_terminal_and_names_tail():
    """The TUI needs a terminal, and the read-only view is what works without one."""
    result, client = run_cli(["attach", "a1"])
    assert result.exit_code != 0
    assert "mael agent tail -f a1" in result.output
    assert client.calls == []


def test_approve_names_the_request_when_given_one():
    """With several waits open the daemon needs to be told which."""
    _, client = run_cli(["approve", "a1", "--request", "req-2"])
    assert client.calls[0]["request"] == "req-2"


def test_approve_names_no_request_by_default():
    """Omitted means "the only one", which is what a single wait needs."""
    _, client = run_cli(["approve", "a1"])
    assert client.calls[0].get("request", "") == ""


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
# `tail` opens its own connection through `open_connection`, so a test serves a
# real `AgentDaemon` over a `socketpair` on the CLI's own loop.


@contextmanager
def _serving(state, spy: list[dict] | None = None):
    """Serve one agent in ``state`` to the next connection, for the body."""
    daemon = AgentDaemon("unused.sock")
    proc = MagicMock()
    proc.pid = 4242
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

    serving: list[asyncio.Task] = []

    async def fake_open(socket_path: str, *, limit: int = agent_transport.STREAM_LIMIT):
        client_end, daemon_end = socket.socketpair(socket.AF_UNIX)
        reader, writer = await asyncio.open_unix_connection(
            sock=daemon_end, limit=limit
        )
        serving.append(asyncio.ensure_future(daemon._on_client(reader, writer)))
        return await asyncio.open_unix_connection(sock=client_end, limit=limit)

    original_open = agent_transport.open_connection
    agent_transport.open_connection = fake_open
    try:
        yield
    finally:
        agent_transport.open_connection = original_open
        for task in serving:
            task.cancel()


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


def test_resume_sends_the_agent_id(prompt_file):
    result, client = run_cli(["resume", "a1"], [{"ok": True, "id": "a1"}])
    assert result.exit_code == 0
    # The prompt file rides along: the record may predate the daemon keeping one.
    assert client.calls == [
        {"cmd": "resume", "id": "a1", "system_prompt_file": str(prompt_file)}
    ]


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
    """A stopped row as the daemon sends it: with no ``task``, which the CLI joins."""
    row = {
        "id": "s1",
        "session": "sess-1",
        "age": "2h",
        "branch": "feat/x",
        "label": "Improve plan mode",
        "cwd": "/w/alpha",
        "model": "",
        "mode": "",
        "modified_at": 1.0,
    }
    row.update(kw)
    return row


def test_the_stopped_listing_names_the_task_each_session_ran_for():
    """The daemon knows no tasks, so the CLI joins them on the session id."""
    result, _ = run_cli(["list", "--stopped"], [{"agents": [stopped_row()]}])
    assert result.exit_code == 0
    assert "2026-09-04.2" in result.output


def test_the_stopped_json_carries_the_task_too():
    result, _ = run_cli(
        ["list", "--stopped", "--json"],
        [{"agents": [stopped_row(), stopped_row(id="s2", session="sess-2")]}],
    )
    rows = json.loads(result.output)
    assert [row["task"] for row in rows] == ["2026-09-04.2", ""]


def test_a_listing_opens_the_task_table_once_not_once_per_session(task_table):
    """~800 transcripts must not mean ~800 SQLite connections."""
    rows = [stopped_row(id=f"s{i}", session=f"sess-{i}") for i in range(20)]
    run_cli(["list", "--stopped", "--json"], [{"agents": rows}])
    assert len(task_table) == 1


def test_the_all_listing_joins_only_the_stopped_rows():
    running = build_agent_row(replay("normal-turn.jsonl"))
    result, _ = run_cli(
        ["list", "--all", "--json"], [{"agents": [running, stopped_row()]}]
    )
    rows = json.loads(result.output)
    assert "task" not in rows[0]
    assert rows[1]["task"] == "2026-09-04.2"


def test_an_unreadable_task_table_blanks_the_column_and_says_so(monkeypatch):
    """A listing is worth more than its task column."""

    def broken():
        raise OSError("disk gone")

    monkeypatch.setattr(agent_cli, "open_task_table", broken)
    result, _ = run_cli(["list", "--stopped"], [{"agents": [stopped_row()]}])
    assert result.exit_code == 0
    assert "Improve plan mode" in result.output
    assert "disk gone" in result.output
    result, _ = run_cli(["list", "--stopped", "--json"], [{"agents": [stopped_row()]}])
    assert json.loads(result.stdout)[0]["task"] == ""


def test_no_notebook_root_fails_the_listing_rather_than_blank_it(monkeypatch):
    """A blank column would hide a misconfigured root behind a listing that works."""

    def unset():
        raise NotebookRootUnset()

    monkeypatch.setattr(agent_cli, "open_task_table", unset)
    result, _ = run_cli(["list", "--stopped"], [{"agents": [stopped_row()]}])
    assert result.exit_code == 1, result.output
    assert NOTEBOOK_ROOT_UNSET_MESSAGE in result.output
    assert "Improve plan mode" not in result.output


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


def test_the_all_listing_renders_both_shapes_of_row_legibly():
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


def test_the_stopped_listing_prints_its_columns_in_order():
    """The columns are what a user reads, and nothing else pins them.

    There is no ``kind`` column: a session is listed only when it has a spawn
    record, and only the daemon writes one, so the column would read ``mael``
    on every row.
    """
    result, _ = run_cli(["list", "--stopped"], [{"agents": [stopped_row()]}])
    assert result.exit_code == 0
    header = result.output.splitlines()[0].split()
    assert header == ["id", "age", "task", "branch", "label", "cwd"]


# --- subagents --------------------------------------------------------------


def test_tail_on_a_subagent_prints_that_ring_and_stops():
    """The parent's words are not there; the subagent's are."""
    with _serving(replay("subagent-turn.jsonl")):
        result = CliRunner().invoke(agent_cli.agent, ["tail", "a1.1"])
    assert result.exit_code == 0
    assert "I'll look for the `docs/dev` directory." in result.output
    assert "The subagent" not in result.output


def test_list_shows_the_parent_column():
    state = replay("subagent-turn.jsonl")
    rows = [build_agent_row(state), *build_subagent_rows(state)]
    result, _ = run_cli(["list"], [{"agents": rows}])
    assert result.output.splitlines()[0].split() == agent_cli.LIST_COLUMNS
    assert "a1.1" in result.output
    assert "List and summarise docs/dev" in result.output


def test_show_on_a_subagent_prints_the_subagent():
    detail = build_subagent_detail(replay("subagent-turn.jsonl"), "a1.1")
    result, client = run_cli(["show", "a1.1"], [{"agent": detail}])
    assert client.calls == [{"cmd": "show", "id": "a1.1"}]
    assert result.exit_code == 0
    assert "a1.1" in result.output
    assert "parent:       a1" in result.output
    assert "description:  List and summarise docs/dev" in result.output
    assert "`docs/dev` exists" in result.output
    assert "Subagents:" not in result.output


def test_show_on_a_parent_lists_its_subagents():
    detail = build_agent_detail(replay("subagent-turn.jsonl"))
    result, _ = run_cli(["show", "a1"], [{"agent": detail}])
    assert "Subagents:" in result.output
    assert "a1.1" in result.output
    assert "exited(0)" in result.output
    assert "List and summarise docs/dev" in result.output


def test_show_on_a_parent_with_no_subagents_says_nothing_of_them():
    detail = build_agent_detail(replay("normal-turn.jsonl"))
    result, _ = run_cli(["show", "a1"], [{"agent": detail}])
    assert "Subagents:" not in result.output


def test_show_names_the_subagent_a_wait_came_from():
    detail = build_agent_detail(
        replay("subagent-permission.jsonl", stop_before_control=True)
    )
    result, _ = run_cli(["show", "a1"], [{"agent": detail}])
    assert "Waiting on: WebFetch (from a1.1)" in result.output
    assert "mael agent approve a1" in result.output


class TestStopAgentsInWorktree:
    """`mael close` stops the daemon's agents before it signals any pid.

    A driven agent is a `claude` process in the worktree, so the pid sweep
    would find and signal it. The daemon reads that as an unexpected exit and
    records a crash, leaving a phantom in `mael agent list --all`.
    """

    async def _stop(self, replies, path="/wt/alpha"):
        client = RecordingDaemonClient(replies=list(replies))
        agent_transport.client_factory = lambda **_: client
        try:
            return await stop_agents_in_worktree(Path(path)), client
        finally:
            agent_transport.client_factory = SocketAsyncDaemonClient

    async def test_stops_only_the_agents_in_that_worktree(self):
        rows = [
            {"id": "a1", "cwd": "/wt/alpha"},
            {"id": "a2", "cwd": "/wt/bravo"},
            {"id": "a3", "cwd": "/wt/alpha"},
        ]
        messages, client = await self._stop([{"agents": rows}])
        assert client.calls == [
            {"cmd": "list"},
            {"cmd": "stop", "id": "a1"},
            {"cmd": "stop", "id": "a3"},
        ]
        assert messages == ["agent a1: stopped", "agent a3: stopped"]

    async def test_no_agents_there_sends_no_stop(self):
        messages, client = await self._stop(
            [{"agents": [{"id": "a2", "cwd": "/wt/bravo"}]}]
        )
        assert client.calls == [{"cmd": "list"}]
        assert messages == []

    async def test_an_unreachable_daemon_is_silent(self):
        # The close must not fail because the daemon is down; the pid sweep
        # that follows still tears the session down.
        messages, client = await self._stop([unreachable("/x")])
        assert messages == []

    async def test_a_refused_stop_is_reported_not_raised(self):
        rows = [{"id": "a1", "cwd": "/wt/alpha"}]
        messages, _ = await self._stop(
            [{"agents": rows}, {"error": "agent a1 has exited"}]
        )
        assert messages == ["agent a1: agent a1 has exited"]


class TestResolveRootHasNoFallback:
    """No fallback: an unset root is an error, never a guessed directory.

    `~/.maelstrom` used to be the fallback, so a command with no root reached
    the everyday daemon. Every environment now names its own root, and a
    command that finds none must say so rather than reach for someone else's
    agents.
    """

    def test_an_unset_root_raises(self, monkeypatch):
        from maelstrom.agent_transport import RootUnset, require_root

        monkeypatch.delenv("MAEL_AGENT_ROOT", raising=False)
        with pytest.raises(RootUnset):
            require_root()

    def test_a_tilde_expands(self, monkeypatch):
        """The value is written by hand in `.env`, so `~` reaches it. An
        unexpanded `~` makes a directory named `~` in the current directory."""
        from maelstrom.agent_transport import require_root

        monkeypatch.setenv("HOME", "/home/tester")
        monkeypatch.setenv("MAEL_AGENT_ROOT", "~/.maelstrom/daemons/bravo")
        assert require_root() == Path("/home/tester/.maelstrom/daemons/bravo")


def test_an_unreachable_daemon_is_named_with_the_command_that_starts_one(
    tmp_path, monkeypatch
):
    """The message is behaviour: `gc` and `status` match on it."""
    root = tmp_path / "chosen"
    monkeypatch.setenv("MAEL_AGENT_ROOT", str(root))
    result, _ = run_cli(
        ["list"],
        replies=[unreachable(root)],
    )
    assert result.exit_code == 1
    assert f"No agent daemon on {root}" in result.output
    assert "mael self-env start" in result.output


class TestTailRaw:
    """`mael agent tail --raw`: the child's stream as JSON, one event per line.

    The recorder for `tests/fixtures/agent_events/`. The rendered form drops
    every event `_render` has no line for -- `system`/`task_*` above all --
    which is exactly what a fixture needs, so `--raw` prints the events
    themselves. The daemon's own `mael_*` markers are not the child's, so
    they do not appear, and neither does the `mael_seq`/`mael_ts` stamp.
    """

    def run_tail(self, monkeypatch, argv, backlog, agent_id="a1"):
        """Drive `tail` over the scripted async client, and return its output."""
        from maelstrom.orchestrator.daemon_bridge import ScriptedAsyncDaemonClient

        client = ScriptedAsyncDaemonClient(
            rows={agent_id: build_agent_row(replay("normal-turn.jsonl"))},
            backlog={agent_id: list(backlog)},
        )
        monkeypatch.setattr(agent_cli, "SocketAsyncDaemonClient", lambda: client)
        return CliRunner().invoke(agent_cli.agent, argv)

    def lines(self, result):
        """The output as parsed JSON, one object per line."""
        return [json.loads(line) for line in result.output.splitlines() if line.strip()]

    def test_every_event_is_one_json_line(self, monkeypatch):
        """The rendered form drops `system` events; a fixture needs them."""
        backlog = [
            {"type": "system", "subtype": "task_started", "task_id": "t1"},
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "hi"}]},
            },
            {"type": "result", "subtype": "success"},
        ]
        result = self.run_tail(monkeypatch, ["tail", "--raw", "a1"], backlog)
        assert result.exit_code == 0, result.output
        assert [e["type"] for e in self.lines(result)] == [
            "system",
            "assistant",
            "result",
        ]

    def test_the_daemons_own_stamp_is_not_in_the_output(self, monkeypatch):
        """`mael_seq` and `mael_ts` are the daemon's, not the child's."""
        backlog = [{"type": "result", "subtype": "success"}]
        result = self.run_tail(monkeypatch, ["tail", "--raw", "a1"], backlog)
        [event] = self.lines(result)
        assert "mael_seq" not in event
        assert "mael_ts" not in event

    def test_the_daemons_own_markers_are_not_events(self, monkeypatch):
        """`mael_agent_detail` and `mael_backlog_end` open and close the stream."""
        backlog = [{"type": "result", "subtype": "success"}]
        result = self.run_tail(monkeypatch, ["tail", "--raw", "a1"], backlog)
        assert [e["type"] for e in self.lines(result)] == ["result"]

    def test_the_exit_marker_does_not_break_the_json(self, monkeypatch):
        """The exit notice is prose, so a recording must not carry it."""
        from maelstrom.orchestrator.daemon_bridge import ScriptedAsyncDaemonClient

        client = ScriptedAsyncDaemonClient(
            rows={"a1": build_agent_row(replay("normal-turn.jsonl"))},
            backlog={"a1": [{"type": "result", "subtype": "success"}]},
        )
        monkeypatch.setattr(agent_cli, "SocketAsyncDaemonClient", lambda: client)

        async def drive():
            task = asyncio.create_task(agent_cli._tail("a1", True, True))
            # Let the backlog drain, then end the agent as the host would.
            for _ in range(20):
                await asyncio.sleep(0)
            client.push("a1", {"type": AGENT_EXITED, "exit_code": 0})
            await task

        with CliRunner().isolation() as (out, _err, _):
            asyncio.run(drive())
            captured = out.getvalue().decode()
        # The exact events, so an empty capture cannot pass.
        assert [json.loads(line) for line in captured.splitlines() if line.strip()] == [
            {"type": "result", "subtype": "success"}
        ]

    def test_without_raw_the_rendered_form_is_unchanged(self, monkeypatch):
        """The flag is additive: `tail` on its own still renders lines."""
        backlog = [{"type": "result", "subtype": "success"}]
        result = self.run_tail(monkeypatch, ["tail", "a1"], backlog)
        assert result.output.strip() == "— turn complete (success)"


# --- mael agent cost ---------------------------------------------------------


def seed_ledger(tmp_path, *snapshots: dict) -> None:
    """Write ``snapshots`` to the ledger the ``cost`` command reads."""
    db = open_state_db(tmp_path / "state.db")
    try:
        store = SqliteMilestoneStore(db)
        for snapshot in snapshots:
            asyncio.run(store.record(snapshot))
    finally:
        db.close()


def snapshot(
    name: str,
    own: int,
    sub: int,
    cost: float,
    agent_id: str = "a1",
    recognised: bool = True,
) -> dict:
    """One milestone, as the server writes it.

    ``recognised`` is stated rather than looked up in :data:`MILESTONES`: a
    test that recomputes the answer from the constant is asserting the
    configuration back at itself.
    """
    return {
        "agent_id": agent_id,
        "name": name,
        "at": "2026-09-21T10:00:00Z",
        "recognised": recognised,
        "own_tokens": own,
        "subagent_tokens": sub,
        "cost_usd": cost,
    }


def test_cost_reports_each_stage_and_names_the_dollars_parent_only(
    tmp_path, monkeypatch
):
    """The whole point: which stage was expensive, and what the $ covers."""
    monkeypatch.setenv("MAEL_NOTEBOOK_ROOT", str(tmp_path))
    assert CliRunner().invoke(admin_cli.cmd_migrate, []).exit_code == 0
    seed_ledger(
        tmp_path,
        snapshot("planned", 10_000, 0, 0.5),
        snapshot("built", 75_000, 30_000, 2.6),
    )

    result = CliRunner().invoke(agent_cli.agent, ["cost"])

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert "105,000 tokens (75,000 own + 30,000 subagent)" in lines[0]
    # Named every time the figure is printed: it is not the tree's.
    assert "own requests only" in lines[0]
    assert lines[1].split() == agent_cli.COST_COLUMNS
    # Each stage's own spend, beside the total by then.
    assert [line.split()[0] for line in lines[3:5]] == ["planned", "built"]
    assert lines[3].split()[1:] == ["10,000", "10,000", "0", "10,000", "0.5000"]
    assert lines[4].split()[1:] == ["95,000", "65,000", "30,000", "105,000", "2.1000"]


def test_cost_makes_no_daemon_call(tmp_path, monkeypatch):
    """It reads the ledger, which is what lets a stopped agent still report."""
    monkeypatch.setenv("MAEL_NOTEBOOK_ROOT", str(tmp_path))
    assert CliRunner().invoke(admin_cli.cmd_migrate, []).exit_code == 0
    seed_ledger(tmp_path, snapshot("built", 40_000, 0, 1.0))

    result, client = run_cli(["cost", "a1"])

    assert result.exit_code == 0, result.output
    assert client.calls == []
    assert "40,000 tokens" in result.output


def test_cost_flags_a_stage_name_the_flow_does_not_declare(tmp_path, monkeypatch):
    monkeypatch.setenv("MAEL_NOTEBOOK_ROOT", str(tmp_path))
    assert CliRunner().invoke(admin_cli.cmd_migrate, []).exit_code == 0
    seed_ledger(tmp_path, snapshot("deployed", 5_000, 0, 0.1, recognised=False))

    result = CliRunner().invoke(agent_cli.agent, ["cost"])

    assert result.exit_code == 0, result.output
    assert "deployed (?)" in result.output


def test_cost_does_not_flag_the_row_that_closes_the_ledger(tmp_path, monkeypatch):
    """`<final>` is Maelstrom's own, not a name an agent mistyped.

    "(?)" says the agent wrote a word we do not know. The closing row is not
    the agent's, so drawing it there would send a reader looking for a typo.
    """
    monkeypatch.setenv("MAEL_NOTEBOOK_ROOT", str(tmp_path))
    assert CliRunner().invoke(admin_cli.cmd_migrate, []).exit_code == 0
    seed_ledger(tmp_path, snapshot("<final>", 5_000, 0, 0.1))

    result = CliRunner().invoke(agent_cli.agent, ["cost"])

    assert result.exit_code == 0, result.output
    assert "<final>" in result.output
    assert "(?)" not in result.output


def test_cost_says_so_when_nothing_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("MAEL_NOTEBOOK_ROOT", str(tmp_path))
    assert CliRunner().invoke(admin_cli.cmd_migrate, []).exit_code == 0

    result = CliRunner().invoke(agent_cli.agent, ["cost"])

    assert result.exit_code == 0, result.output
    assert "No milestones recorded." in result.output


def test_cost_json_carries_the_stage_deltas(tmp_path, monkeypatch):
    monkeypatch.setenv("MAEL_NOTEBOOK_ROOT", str(tmp_path))
    assert CliRunner().invoke(admin_cli.cmd_migrate, []).exit_code == 0
    seed_ledger(
        tmp_path,
        snapshot("planned", 10_000, 0, 0.5),
        snapshot("built", 75_000, 30_000, 2.6),
    )

    result = CliRunner().invoke(agent_cli.agent, ["cost", "--json"])

    [agent] = json.loads(result.output)
    assert agent["total_tokens"] == 105_000
    assert [s["name"] for s in agent["stages"]] == ["planned", "built"]
