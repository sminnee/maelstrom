"""``mael agent`` — start, watch, answer and teleport into daemon-driven agents.

The thin client of :mod:`maelstrom.agent_server`, speaking only the wire
contract in :mod:`maelstrom.agent_wire`. Every command is one NDJSON
round-trip to the daemon's control socket, so this module holds no state and
does no agent logic: it parses flags, sends a command, and prints the reply.
The daemon builds each row; this module draws it, and joins each stopped row
to its task. ``mael agent daemon`` is in
:mod:`maelstrom.agent_daemon_cli`.

No command starts a daemon. The environment manager does: `mael self-env
start` runs the everyday daemon and `mael env start` runs this worktree's.
A command that finds none says so, naming the root — see
``docs/dev/agent-daemon.md``.
"""

import asyncio
import json
import shlex
import sys
from pathlib import Path
from typing import Any

import click

from .agent_cost import AgentCost, Stage, build_cost_report
from .agent_store import SqliteAgentStore, SqliteMilestoneStore, register_agent
from .agent_transport import (
    SocketAsyncDaemonClient,
)
from .agent_transport import client as daemon_client
from .agent_wire import (
    AGENT_DETAIL,
    AGENT_EXITED,
    AWAITING_PERMISSION,
    AWAITING_PLAN_REVIEW,
    AWAITING_QUESTION,
    BACKLOG_END,
    MODES,
    SCOPE_ALL,
    SCOPE_RUNNING,
    SCOPE_STOPPED,
    SEQ_KEY,
    TRUNCATED,
    TS_KEY,
    build_resume_payload,
    build_start_payload,
)
from .cli_async import AsyncGroup
from .context import resolve_context
from .harness_model import resolve_execute_model
from .notebook_root import NotebookRootUnset
from .shared_dir import agent_prompt_file
from .state_db.migrate import open_state_db
from .state_db.paths import get_state_db_path
from .state_db.types import StateDbError
from .table_cli import draw_table
from .task_cli import open_task_table
from .util import now_iso

#: Columns ``mael agent list`` prints, in order.
LIST_COLUMNS = [
    "id",
    "parent",
    "description",
    "state",
    "mode",
    "waiting_on",
    "last_note",
    "last_message",
    "cwd",
    "model",
    "cost",
]

#: Columns ``mael agent show`` prints for a parent's subagents, in order. No
#: ``last_note``: a subagent writes none, so the column would always be empty.
SUBAGENT_COLUMNS = ["id", "state", "description", "last_message"]


async def _send(payload: dict[str, Any]) -> dict[str, Any]:
    """Send one command, printing the daemon's error and exiting on failure.

    A ``warning`` is not a failure: the command did what was asked, and
    something alongside it did not. It prints and the command still succeeds.
    """
    reply = await daemon_client().request(payload)
    if "error" in reply:
        click.echo(f"Error: {reply['error']}", err=True)
        sys.exit(1)
    if reply.get("warning"):
        click.echo(f"Warning: {reply['warning']}", err=True)
    return reply


@click.group(cls=AsyncGroup)
def agent() -> None:
    """Drive Claude agents over a stream-json pipe."""


@agent.command("start")
@click.argument("cwd", type=click.Path(exists=True, file_okay=False), default=".")
@click.option("--prompt", "-p", default="", help="Opening prompt for the agent.")
@click.option("--mode", default=None, help="Permission mode, e.g. auto or plan.")
@click.option("--model", default=None, help="Model for the agent.")
@click.option(
    "--execute-model",
    "execute_model",
    default=None,
    help="Model to switch to when the agent's plan is approved "
    "(default: none, which keeps it on --model throughout). Claude models only.",
)
@click.option(
    "--session-id",
    "session_id",
    default=None,
    help="Pin the Claude session id the agent reports.",
)
async def cmd_start(
    cwd: str,
    prompt: str,
    mode: str | None,
    model: str | None,
    execute_model: str | None,
    session_id: str | None,
) -> None:
    """Start an agent in CWD."""
    if execute_model is not None:
        try:
            resolve_execute_model(execute_model)
        except ValueError as exc:
            raise click.UsageError(str(exc))
    reply = await _send(
        build_start_payload(
            Path(cwd).resolve(),
            prompt=prompt,
            permission_mode=mode,
            model=model,
            execute_model=execute_model,
            session_id=session_id,
            system_prompt_file=agent_prompt_file(),
        )
    )
    click.echo(reply["id"])


@agent.command("list")
@click.option("--json", "as_json", is_flag=True, help="Emit rows as JSON.")
@click.option(
    "--stopped",
    is_flag=True,
    help="Show sessions that have stopped and can be resumed, not running agents.",
)
@click.option("--all", "show_all", is_flag=True, help="Show both.")
@click.option(
    "-w",
    "--worktree",
    "worktree_opt",
    help="Only sessions from this worktree (project.worktree).",
)
@click.option("--project", help="Only sessions from this project.")
async def cmd_list(
    as_json: bool,
    stopped: bool,
    show_all: bool,
    worktree_opt: str | None,
    project: str | None,
) -> None:
    """Show every agent, and what each waiting one is waiting on.

    ``--stopped`` shows what has stopped instead: every session
    ``mael agent resume`` can bring back. A session is listed only when the
    daemon started it, because a resume reads the daemon's spawn record. A
    session you started by hand has no record, and ``claude --resume`` brings
    that one back.
    """
    if stopped and show_all:
        raise click.ClickException("--stopped and --all cannot be used together")
    cwd = _filter_cwd(worktree_opt, project)
    payload: dict[str, Any] = {"cmd": "list"}
    if show_all:
        payload["scope"] = SCOPE_ALL
    # A filter only means anything against stopped sessions, so it implies the scope.
    elif stopped or cwd:
        payload["scope"] = SCOPE_STOPPED
    if cwd:
        payload["cwd"] = cwd
    rows = (await _send(payload)).get("agents", [])
    if payload.get("scope", SCOPE_RUNNING) != SCOPE_RUNNING:
        rows = await _with_tasks(rows)
    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return
    scope = payload.get("scope", SCOPE_RUNNING)
    if not rows:
        click.echo(_NOTHING_FOUND[scope])
        return
    _draw_rows(rows, scope)


def _is_stopped(row: dict[str, Any]) -> bool:
    """Whether ``row`` is a stopped session rather than a running agent.

    A :class:`~maelstrom.agent_wire.StoppedRow` carries no ``state``, and an
    :class:`~maelstrom.agent_wire.AgentRow` always does. ``--all`` mixes the two.
    """
    return "state" not in row


async def _with_tasks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``rows`` with the task each stopped session ran for, as ``task``.

    The daemon knows no tasks, so the join is here. A running row is left as
    it is. The table is opened once for
    the whole listing: a per-session open would build a connection hundreds of
    times.

    A listing is worth more than its task column, so a table failure blanks the
    column and says why on stderr. A missing notebook root fails the command:
    a blank column would hide a misconfigured root behind a listing that works.
    """
    stopped = [row for row in rows if _is_stopped(row)]
    if not stopped:
        return rows
    tasks: dict[str, str] = {}
    try:
        table = open_task_table()
        for row in stopped:
            found = await table.find_by_session_id(row["session"])
            tasks[row["session"]] = found.id if found else ""
    except NotebookRootUnset as exc:
        raise click.ClickException(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        click.echo(f"Warning: could not read the task table: {exc}", err=True)
        tasks = {}
    return [
        {**row, "task": tasks.get(row["session"], "")} if _is_stopped(row) else row
        for row in rows
    ]


#: The columns ``--stopped`` prints, in order.
STOPPED_COLUMNS = ["id", "age", "task", "branch", "label", "cwd"]


#: What an empty listing says, per scope. Each names what was looked for, so a
#: user reading "No stopped sessions." knows the running ones were not checked.
_NOTHING_FOUND = {
    SCOPE_RUNNING: "No agents running.",
    SCOPE_STOPPED: "No stopped sessions.",
    SCOPE_ALL: "No agents running or stopped.",
}


def _draw_rows(rows: list[dict[str, Any]], scope: str) -> None:
    """Draw ``rows`` under the columns their scope calls for.

    A running row and a stopped row share almost no fields, so ``--all`` draws
    two tables rather than one. Under a single column set each row would render
    the other kind's columns as blank cells, and the stopped rows would lose the
    very fields that make them worth listing.
    """
    if scope != SCOPE_ALL:
        columns = STOPPED_COLUMNS if scope == SCOPE_STOPPED else LIST_COLUMNS
        draw_table(rows, columns)
        return
    running = [row for row in rows if not _is_stopped(row)]
    stopped = [row for row in rows if _is_stopped(row)]
    if running:
        draw_table(running, LIST_COLUMNS)
    if stopped:
        if running:
            click.echo()
        click.echo("Stopped, resumable:")
        draw_table(stopped, STOPPED_COLUMNS)


def _filter_cwd(worktree_opt: str | None, project: str | None) -> str:
    """The path a ``-w``/``--project`` filter means, or ``""``.

    Resolved here and sent as a plain path: mapping ``foo.alpha`` to a directory
    reads config and walks the filesystem, which is CLI-layer work. The daemon
    never learns what a project is.
    """
    if not worktree_opt and not project:
        return ""
    try:
        context = resolve_context(
            worktree_opt or project,
            require_worktree=bool(worktree_opt),
            require_project=bool(project),
            arg_is_project=bool(project) and not worktree_opt,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    path = context.worktree_path if worktree_opt else context.project_path
    if path is None:
        raise click.ClickException(f"could not resolve {worktree_opt or project}")
    return str(path)


@agent.command("show")
@click.argument("agent_id")
@click.option("--json", "as_json", is_flag=True, help="Emit the detail as JSON.")
async def cmd_show(agent_id: str, as_json: bool) -> None:
    """Show one agent in full: what it said, and what it waits on."""
    detail = (await _send({"cmd": "show", "id": agent_id}))["agent"]
    if as_json:
        click.echo(json.dumps(detail, indent=2))
        return
    _print_detail(detail)


def _answer_hint(detail: dict[str, Any]) -> str:
    """The command that resolves this agent's wait, or ``""`` when none does."""
    agent_id = detail["id"]
    kind = detail.get("waiting_kind", "")
    if kind == AWAITING_QUESTION:
        options = [
            option["label"]
            for question in detail.get("questions", [])
            for option in question.get("options", [])
        ]
        choice = options[0] if options else "<choice>"
        # An option label is model-written text. Unquoted, one carrying a `$` or
        # a backtick becomes a live substitution the moment a user pastes it.
        return f"mael agent answer {agent_id} {shlex.quote(choice)}"
    if kind == AWAITING_PLAN_REVIEW:
        return f"mael agent approve {agent_id}"
    if kind == AWAITING_PERMISSION:
        return f"mael agent approve {agent_id}   (or deny)"
    return ""


def _print_detail(detail: dict[str, Any]) -> None:
    """Render one detail, an agent's or a subagent's, as ``show`` returned it.

    An agent shows its state, its words, its wait and its subagents. A
    subagent's detail has the same keys with the last two empty, so it prints
    its state and its words and stops.
    """
    for key in (
        "id",
        "parent",
        "description",
        "state",
        "session",
        "cwd",
        "model",
        "cost",
    ):
        if detail.get(key):
            click.echo(f"{key + ':':<13} {detail[key]}")

    if detail.get("message"):
        click.echo(f"\n{detail['message']}")

    if detail.get("plan"):
        click.echo(f"\nPlan:\n{detail['plan']}")
    if detail.get("plan_file"):
        click.echo(f"\nPlan file: {detail['plan_file']}")

    for question in detail.get("questions", []):
        header = question.get("header") or "Question"
        multi = " (choose any)" if question.get("multi_select") else ""
        click.echo(f"\n{header}{multi}: {question['question']}")
        for option in question.get("options", []):
            description = option.get("description", "")
            suffix = f" — {description}" if description else ""
            click.echo(f"  {option['label']}{suffix}")

    if detail.get("waiting_tool") and not detail.get("questions"):
        origin = detail.get("waiting_subagent", "")
        suffix = f" (from {origin})" if origin else ""
        click.echo(f"\nWaiting on: {detail['waiting_tool']}{suffix}")
        if detail.get("waiting_input"):
            click.echo(f"  {json.dumps(detail['waiting_input'])[:400]}")

    hint = _answer_hint(detail)
    if hint:
        click.echo(f"\nAnswer with:  {hint}")

    if detail.get("subagents"):
        click.echo("\nSubagents:")
        draw_table(detail["subagents"], SUBAGENT_COLUMNS)


@agent.command("say")
@click.argument("agent_id")
@click.argument("text")
async def cmd_say(agent_id: str, text: str) -> None:
    """Send TEXT to an agent as a user message."""
    await _send({"cmd": "say", "id": agent_id, "text": text})


@agent.command("run")
@click.argument("agent_id")
@click.argument("command")
async def cmd_run(agent_id: str, command: str) -> None:
    """Run COMMAND in the agent's directory and give it the output.

    The same thing a `!` line does in the Claude Code terminal: the host runs
    it, and the command and its output become context. Maelstrom asks the agent
    for nothing, though the agent often remarks on what it read.
    """
    await _send({"cmd": "run", "id": agent_id, "command": command})


@agent.command("answer")
@click.argument("agent_id")
@click.argument("choice")
@click.option(
    "--request",
    "request_id",
    default="",
    help="Which wait to answer. Needed only when several are open.",
)
async def cmd_answer(agent_id: str, choice: str, request_id: str) -> None:
    """Answer an agent's pending question with CHOICE."""
    await _send(
        {"cmd": "answer", "id": agent_id, "choice": choice, "request": request_id}
    )


@agent.command("approve")
@click.argument("agent_id")
@click.option(
    "--request",
    "request_id",
    default="",
    help="Which wait to answer. Needed only when several are open.",
)
async def cmd_approve(agent_id: str, request_id: str) -> None:
    """Approve an agent's pending plan or tool call."""
    await _send({"cmd": "approve", "id": agent_id, "request": request_id})


@agent.command("deny")
@click.argument("agent_id")
@click.option("--reason", "-r", default="", help="Why, shown to the agent.")
@click.option(
    "--request",
    "request_id",
    default="",
    help="Which wait to answer. Needed only when several are open.",
)
async def cmd_deny(agent_id: str, reason: str, request_id: str) -> None:
    """Deny an agent's pending plan or tool call."""
    await _send(
        {"cmd": "deny", "id": agent_id, "reason": reason, "request": request_id}
    )


@agent.command("interrupt")
@click.argument("agent_id")
async def cmd_interrupt(agent_id: str) -> None:
    """Abandon the turn an agent is running, leaving the agent alive.

    A pending permission ask or question is denied first. ``stop`` is what
    ends an agent.
    """
    await _send({"cmd": "interrupt", "id": agent_id})


@agent.command("recover")
@click.argument("agent_id")
async def cmd_recover(agent_id: str) -> None:
    """Clear a poisoned context and send the agent its work again.

    For an agent that answers but does no work. The child keeps running and
    keeps its session, so the task link survives.
    """
    await _send({"cmd": "recover", "id": agent_id})


@agent.command("set-mode")
@click.argument("agent_id")
@click.argument("mode", type=click.Choice(MODES))
async def cmd_set_mode(agent_id: str, mode: str) -> None:
    """Change the permission mode of a running agent."""
    await _send({"cmd": "set-mode", "id": agent_id, "mode": mode})


@agent.command("stop")
@click.argument("agent_id")
async def cmd_stop(agent_id: str) -> None:
    """Stop an agent."""
    await _send({"cmd": "stop", "id": agent_id})


@agent.command("register")
@click.argument("agent_id")
@click.option(
    "--task-id",
    default="",
    help="The Task id this agent belongs to.",
)
async def cmd_register(agent_id: str, task_id: str) -> None:
    """Adopt a live agent that has no Agent record, by hand.

    The orchestrator's own `list` adopts such an agent — see
    ``daemon_bridge.DaemonRouter._adopt``. This is for a record that must exist
    before the orchestrator next polls, or when no orchestrator runs.

    Every other `mael agent` command speaks only to the daemon socket. This one
    also opens the state database, which is the cost of writing a record
    outside the router.

    This socket is the Claude agent daemon, the only harness `mael agent`
    reaches, so the harness is always ``claude`` — see
    :func:`maelstrom.agent_store.register_agent`.
    """
    reply = await daemon_client().request({"cmd": "list"})
    if "error" in reply:
        click.echo(f"Error: {reply['error']}", err=True)
        sys.exit(1)
    row = next((r for r in reply.get("agents", []) if r.get("id") == agent_id), None)
    if row is None:
        click.echo(f"Error: no live agent {agent_id!r}.", err=True)
        sys.exit(1)
    path = get_state_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = open_state_db(path)
    try:
        try:
            await db.check()
        except StateDbError as exc:
            raise click.ClickException(str(exc)) from exc
        await register_agent(
            SqliteAgentStore(db), agent_id, row, task_id, started_at=now_iso()
        )
    finally:
        db.close()


@agent.command("resume")
@click.argument("agent_id")
@click.option(
    "--text",
    "-t",
    default="",
    help="What to tell the agent on its first turn back.",
)
async def cmd_resume(agent_id: str, text: str) -> None:
    """Start an exited agent again, keeping its id and its conversation.

    ``claude`` writes a transcript for a driven agent, so the conversation
    survives a crashed child, a crashed daemon or a reboot. Without ``--text``
    the agent is told its process ended and to carry on from where it was.
    """
    await _send(
        build_resume_payload(
            agent_id, text=text, system_prompt_file=agent_prompt_file()
        )
    )


@agent.command("attach")
@click.argument("agent_id")
def cmd_attach(agent_id: str) -> None:
    """Teleport into an agent: read what it does, answer it, and interrupt it.

    Raises:
        click.ClickException: If stdin or stdout is not a terminal.
    """
    from .agent_tui import AttachApp

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise click.ClickException(
            f"attach needs a terminal; use `mael agent tail -f {agent_id}` "
            f"to follow without one"
        )
    AttachApp(agent_id, SocketAsyncDaemonClient()).run()


@agent.command("tail")
@click.argument("agent_id")
@click.option("-f", "follow", is_flag=True, help="Keep streaming new events.")
@click.option(
    "--raw",
    is_flag=True,
    help="Print each event as JSON, one per line, for recording a fixture.",
)
async def cmd_tail(agent_id: str, follow: bool, raw: bool) -> None:
    """Read an agent without driving it: print its events, and stop.

    The read-only half of ``attach``. With ``-f`` it keeps streaming; without
    it, it stops where the replayed history ends. Nothing you type reaches the
    agent either way.

    ``--raw`` prints the child's own events as JSON instead of rendering them.
    The rendered form shows only what it has a line for, which leaves out the
    ``system`` events a subagent's life is told in, so a fixture is recorded
    with ``--raw``. See ``docs/dev/agent-daemon.md``.
    """
    # Ctrl-C ends a follow; `cli_async` catches it where the loop is opened,
    # because a cancelled await never re-raises inside the command.
    await _tail(agent_id, follow, raw)


#: How long ``tail`` waits for one line before giving up.
#:
#: A backstop, not a heuristic: the backlog marker is what ends a tail. This
#: only catches a daemon that never sends it, so the command errors instead of
#: hanging.
TAIL_READ_TIMEOUT = 30.0


async def _tail(agent_id: str, follow: bool, raw: bool) -> None:
    """Print an agent's events until the stream ends, driving nothing.

    Without ``follow`` it stops at the backlog marker. Either way the exit
    marker ends it: the agent is gone, so there is nothing left to follow.

    With ``raw`` each event goes out as JSON, and nothing else is printed, so
    every line of a recording parses.
    """
    stream = SocketAsyncDaemonClient().attach(agent_id)
    while True:
        try:
            event = await asyncio.wait_for(
                anext(stream), timeout=None if follow else TAIL_READ_TIMEOUT
            )
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError:
            click.echo("Error: the daemon stopped sending events", err=True)
            sys.exit(1)
        if "error" in event and "type" not in event:
            click.echo(f"Error: {event['error']}", err=True)
            return
        if event.get("type") == BACKLOG_END:
            if not follow:
                return
            continue
        if event.get("type") == AGENT_EXITED:
            if not raw:
                click.echo(f"— agent exited ({event.get('exit_code')})")
            return
        if event.get("type") == TRUNCATED:
            if not raw:
                click.echo(f"— {event.get('dropped')} earlier events dropped")
            continue
        if raw:
            # The detail frame is the daemon's opening summary, not the child's.
            if event.get("type") != AGENT_DETAIL:
                click.echo(json.dumps(_unstamped(event)))
            continue
        text = _render(event)
        if text:
            click.echo(text)


def _unstamped(event: dict[str, Any]) -> dict[str, Any]:
    """``event`` without the daemon's own per-event stamp.

    The daemon adds ``mael_seq`` and ``mael_ts`` to every event it passes on.
    A recording carries neither.
    """
    return {k: v for k, v in event.items() if k not in (SEQ_KEY, TS_KEY)}


def _render(event: dict[str, Any]) -> str:
    """One event as a line to show, or ``""`` for one not worth showing."""
    kind = event.get("type")
    if kind == "assistant":
        parts = []
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "text" and block.get("text"):
                parts.append(block["text"])
            elif block.get("type") == "tool_use":
                parts.append(f"[{block.get('name')}]")
        return "\n".join(parts)
    if kind == "control_request":
        request = event.get("request") or {}
        if request.get("subtype") == "can_use_tool":
            line = f"⏸  waiting: {request.get('tool_name')}"
            description = request.get("description", "")
            return f"{line} — {description}" if description else line
    if kind == "result":
        return f"— turn complete ({event.get('subtype', '')})"
    return ""


#: Columns ``mael agent cost`` prints per stage, in order.
COST_COLUMNS = ["stage", "tokens", "own", "subagent", "total", "cost"]


@agent.command("cost")
@click.argument("agent_id", default="")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
async def cmd_cost(agent_id: str, as_json: bool) -> None:
    """Show what each agent spent, and which stage of its work spent it.

    Reads the milestone ledger, not the daemon, so a stopped agent still
    reports. ``$`` covers the agent's own requests alone; a subagent's spend is
    reported in tokens.
    """
    path = get_state_db_path()
    db = open_state_db(path)
    try:
        try:
            await db.check()
        except StateDbError as exc:
            raise click.ClickException(str(exc)) from exc
        rows = await SqliteMilestoneStore(db).list(agent_id)
    finally:
        db.close()
    report = build_cost_report(rows)
    if as_json:
        click.echo(json.dumps(report, indent=2))
        return
    if not report:
        click.echo(
            f"No milestones recorded for {agent_id}."
            if agent_id
            else "No milestones recorded."
        )
        return
    for index, agent_cost in enumerate(report):
        if index:
            click.echo()
        _draw_cost(agent_cost)


def _draw_cost(agent_cost: AgentCost) -> None:
    """One agent's spend: the totals, then a row per stage.

    The stage rows carry the deltas, because the question is which stage was
    expensive. The cumulative figure rides beside each one so a reader can see
    the total climbing without adding up the column.
    """
    click.echo(
        f"{agent_cost['agent_id']}: "
        f"{agent_cost['total_tokens']:,} tokens "
        f"({agent_cost['own_tokens']:,} own + "
        f"{agent_cost['subagent_tokens']:,} subagent), "
        f"${agent_cost['cost_usd']:.4f} (own requests only)"
    )
    draw_table([_cost_row(stage) for stage in agent_cost["stages"]], COST_COLUMNS)


def _cost_row(stage: Stage) -> dict[str, Any]:
    """One stage as a table row. An unrecognised name says so beside itself."""
    return {
        "stage": stage["name"] if stage["recognised"] else f"{stage['name']} (?)",
        "tokens": f"{stage['delta_tokens']:,}",
        "own": f"{stage['own_delta']:,}",
        "subagent": f"{stage['subagent_delta']:,}",
        "total": f"{stage['total_tokens']:,}",
        "cost": f"{stage['cost_delta']:.4f}",
    }
