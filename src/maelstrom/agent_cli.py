"""``mael agent`` — start, watch, answer and teleport into daemon-driven agents.

The thin CLI over :mod:`maelstrom.agent_server`. Every command is one NDJSON
round-trip to the daemon's control socket, so this module holds no state and
does no agent logic: it parses flags, sends a command, and prints the reply.
Rendering goes through ``build_agent_row`` in the model layer, the way
``session_cli`` renders through ``session_view``.

The first command that needs a daemon starts one, in the transport layer.
``mael agent daemon`` runs one in the foreground instead — see
``docs/dev/agent-daemon.md``.
"""

import asyncio
import json
import shlex
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from .agent_model import (
    AGENT_EXITED,
    AWAITING_PERMISSION,
    AWAITING_PLAN_REVIEW,
    AWAITING_QUESTION,
    BACKLOG_END,
    MODES,
    STOPPED_COLUMNS,
    TRUNCATED,
)
from .agent_server import SCOPE_ALL, SCOPE_RUNNING, SCOPE_STOPPED, AgentDaemon
from .agent_transport import (
    DaemonClient,
    SocketAsyncDaemonClient,
    SocketDaemonClient,
)
from .context import resolve_context
from .table import draw_table

#: Columns ``mael agent list`` prints, in order.
LIST_COLUMNS = [
    "id",
    "state",
    "mode",
    "waiting_on",
    "last_message",
    "cwd",
    "model",
    "cost",
]


#: Overridden by tests to drive commands through ``RecordingDaemonClient``.
_client_factory: Callable[[], DaemonClient] = SocketDaemonClient


def _client() -> DaemonClient:
    """The transport for one CLI invocation."""
    return _client_factory()


def _send(payload: dict[str, Any]) -> dict[str, Any]:
    """Send one command, printing the daemon's error and exiting on failure."""
    reply = _client().request(payload)
    if "error" in reply:
        click.echo(f"Error: {reply['error']}", err=True)
        sys.exit(1)
    return reply


@click.group()
def agent() -> None:
    """Drive Claude agents over a stream-json pipe."""


@agent.command("daemon")
@click.option("--socket", "socket_path", default=None, help="Control socket path.")
def cmd_daemon(socket_path: str | None) -> None:
    """Run the agent daemon in the foreground."""
    daemon = AgentDaemon(socket_path)
    click.echo(f"Listening on {daemon.socket_path}", err=True)
    try:
        asyncio.run(daemon.serve())
    except KeyboardInterrupt:
        pass
    except RuntimeError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@agent.command("start")
@click.argument("cwd", type=click.Path(exists=True, file_okay=False), default=".")
@click.option("--prompt", "-p", default="", help="Opening prompt for the agent.")
@click.option("--mode", default=None, help="Permission mode, e.g. auto or plan.")
@click.option("--model", default=None, help="Model for the agent.")
@click.option(
    "--session-id",
    "session_id",
    default=None,
    help="Pin the Claude session id the agent reports.",
)
def cmd_start(
    cwd: str,
    prompt: str,
    mode: str | None,
    model: str | None,
    session_id: str | None,
) -> None:
    """Start an agent in CWD."""
    reply = _send(
        {
            "cmd": "start",
            "cwd": str(Path(cwd).resolve()),
            "prompt": prompt,
            "mode": mode,
            "model": model,
            "session": session_id,
        }
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
def cmd_list(
    as_json: bool,
    stopped: bool,
    show_all: bool,
    worktree_opt: str | None,
    project: str | None,
) -> None:
    """Show every agent, and what each waiting one is waiting on.

    ``--stopped`` shows what has stopped instead: every session with a
    transcript on disk that is not running, which is every session
    ``mael agent resume`` can bring back. A session maelstrom started keeps its
    spawn record too, so its row also names the model it ran under.
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
    rows = _send(payload).get("agents", [])
    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return
    scope = payload.get("scope", SCOPE_RUNNING)
    if not rows:
        click.echo(_NOTHING_FOUND[scope])
        return
    _draw_rows(rows, scope)


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
    running = [row for row in rows if "state" in row]
    stopped = [row for row in rows if "state" not in row]
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
def cmd_show(agent_id: str, as_json: bool) -> None:
    """Show one agent in full: what it said, and what it waits on."""
    detail = _send({"cmd": "show", "id": agent_id})["agent"]
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
    """Render one agent's detail: its state, its words, and its wait."""
    for key in ("id", "state", "session", "cwd", "model", "cost"):
        if detail.get(key):
            click.echo(f"{key + ':':<9} {detail[key]}")

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
        click.echo(f"\nWaiting on: {detail['waiting_tool']}")
        if detail.get("waiting_input"):
            click.echo(f"  {json.dumps(detail['waiting_input'])[:400]}")

    hint = _answer_hint(detail)
    if hint:
        click.echo(f"\nAnswer with:  {hint}")


@agent.command("say")
@click.argument("agent_id")
@click.argument("text")
def cmd_say(agent_id: str, text: str) -> None:
    """Send TEXT to an agent as a user message."""
    _send({"cmd": "say", "id": agent_id, "text": text})


@agent.command("answer")
@click.argument("agent_id")
@click.argument("choice")
def cmd_answer(agent_id: str, choice: str) -> None:
    """Answer an agent's pending question with CHOICE."""
    _send({"cmd": "answer", "id": agent_id, "choice": choice})


@agent.command("approve")
@click.argument("agent_id")
def cmd_approve(agent_id: str) -> None:
    """Approve an agent's pending plan or tool call."""
    _send({"cmd": "approve", "id": agent_id})


@agent.command("deny")
@click.argument("agent_id")
@click.option("--reason", "-r", default="", help="Why, shown to the agent.")
def cmd_deny(agent_id: str, reason: str) -> None:
    """Deny an agent's pending plan or tool call."""
    _send({"cmd": "deny", "id": agent_id, "reason": reason})


@agent.command("interrupt")
@click.argument("agent_id")
def cmd_interrupt(agent_id: str) -> None:
    """Abandon the turn an agent is running, leaving the agent alive.

    A pending permission ask or question is denied first. ``stop`` is what
    ends an agent.
    """
    _send({"cmd": "interrupt", "id": agent_id})


@agent.command("set-mode")
@click.argument("agent_id")
@click.argument("mode", type=click.Choice(MODES))
def cmd_set_mode(agent_id: str, mode: str) -> None:
    """Change the permission mode of a running agent."""
    _send({"cmd": "set-mode", "id": agent_id, "mode": mode})


@agent.command("stop")
@click.argument("agent_id")
def cmd_stop(agent_id: str) -> None:
    """Stop an agent."""
    _send({"cmd": "stop", "id": agent_id})


@agent.command("resume")
@click.argument("agent_id")
@click.option(
    "--text",
    "-t",
    default="",
    help="What to tell the agent on its first turn back.",
)
def cmd_resume(agent_id: str, text: str) -> None:
    """Start an exited agent again, keeping its id and its conversation.

    ``claude`` writes a transcript for a driven agent, so the conversation
    survives a crashed child, a crashed daemon or a reboot. Without ``--text``
    the agent is told its process ended and to carry on from where it was.
    """
    _send({"cmd": "resume", "id": agent_id, "text": text})


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
def cmd_tail(agent_id: str, follow: bool) -> None:
    """Read an agent without driving it: print its events, and stop.

    The read-only half of ``attach``. With ``-f`` it keeps streaming; without
    it, it stops where the replayed history ends. Nothing you type reaches the
    agent either way.
    """
    try:
        asyncio.run(_tail(agent_id, follow))
    except KeyboardInterrupt:
        pass


#: How long ``tail`` waits for one line before giving up.
#:
#: A backstop, not a heuristic: the backlog marker is what ends a tail. This
#: only catches a daemon that never sends it, so the command errors instead of
#: hanging.
TAIL_READ_TIMEOUT = 30.0


async def _tail(agent_id: str, follow: bool) -> None:
    """Print an agent's events until the stream ends, driving nothing.

    Without ``follow`` it stops at the backlog marker. Either way the exit
    marker ends it: the agent is gone, so there is nothing left to follow.
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
            click.echo(f"— agent exited ({event.get('exit_code')})")
            return
        if event.get("type") == TRUNCATED:
            click.echo(f"— {event.get('dropped')} earlier events dropped")
            continue
        text = _render(event)
        if text:
            click.echo(text)


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
