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
    AWAITING_PERMISSION,
    AWAITING_PLAN_REVIEW,
    AWAITING_QUESTION,
    BACKLOG_END,
)
from .agent_server import AgentDaemon
from .agent_transport import DaemonClient, SocketDaemonClient, resolve_socket_path
from .table import draw_table

#: Columns ``mael agent list`` prints, in order.
LIST_COLUMNS = ["id", "state", "waiting_on", "last_message", "cwd", "model", "cost"]


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
def cmd_list(as_json: bool) -> None:
    """Show every agent, and what each waiting one is waiting on."""
    rows = _send({"cmd": "list"}).get("agents", [])
    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        click.echo("No agents running.")
        return
    draw_table(rows, LIST_COLUMNS)


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

    for message in detail.get("messages", []):
        click.echo(f"\n{message}")

    if detail.get("plan"):
        click.echo(f"\nPlan:\n{detail['plan']}")

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


@agent.command("stop")
@click.argument("agent_id")
def cmd_stop(agent_id: str) -> None:
    """Stop an agent."""
    _send({"cmd": "stop", "id": agent_id})


@agent.command("attach")
@click.argument("agent_id")
def cmd_attach(agent_id: str) -> None:
    """Teleport into an agent: stream its events, and forward what you type."""
    try:
        asyncio.run(_attach(agent_id))
    except KeyboardInterrupt:
        pass


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


async def _connect_attached(
    agent_id: str,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a connection to the daemon and put it in ``attach`` mode.

    Shared by ``attach`` and ``tail``. Both stream the same replay-then-follow
    output; they differ only in what they do with stdin and where they stop.
    """
    path = resolve_socket_path()
    try:
        reader, writer = await asyncio.open_unix_connection(path)
    except OSError as exc:
        click.echo(f"Error: agent daemon not reachable at {path}: {exc}", err=True)
        sys.exit(1)
    writer.write((json.dumps({"cmd": "attach", "id": agent_id}) + "\n").encode())
    await writer.drain()
    return reader, writer


async def _stream(reader: asyncio.StreamReader, *, follow: bool = True) -> None:
    """Print the agent's events until the stream ends.

    With ``follow`` false it stops at the backlog marker instead.
    """
    while True:
        if follow:
            line = await reader.readline()
        else:
            try:
                line = await asyncio.wait_for(
                    reader.readline(), timeout=TAIL_READ_TIMEOUT
                )
            except asyncio.TimeoutError:
                click.echo("Error: the daemon stopped sending events", err=True)
                sys.exit(1)
        if not line:
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "error" in event and "type" not in event:
            click.echo(f"Error: {event['error']}", err=True)
            return
        if event.get("type") == BACKLOG_END:
            if not follow:
                return
            continue
        text = _render(event)
        if text:
            click.echo(text)


async def _tail(agent_id: str, follow: bool) -> None:
    """Stream an agent's events without forwarding anything back to it."""
    reader, writer = await _connect_attached(agent_id)
    try:
        await _stream(reader, follow=follow)
    finally:
        writer.close()


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


async def _attach(agent_id: str) -> None:
    """Stream the agent's events while forwarding stdin lines to it."""
    path = resolve_socket_path()
    reader, writer = await _connect_attached(agent_id)

    async def forward() -> None:
        """Send each typed line to the agent as a user message.

        Typed input goes over its own short-lived connection: the attach
        connection is streaming events and is not answering commands. Opening
        it here rather than through :class:`SocketDaemonClient` keeps it on this
        event loop — that client wraps ``asyncio.run``, which cannot nest.

        ``sys.stdin.readline`` is blocking, so it runs in the default executor
        and the event stream keeps rendering while the user types.
        """
        loop = asyncio.get_running_loop()
        while True:
            text = await loop.run_in_executor(None, sys.stdin.readline)
            if not text:
                return
            text = text.strip()
            if not text:
                continue
            try:
                say_reader, say_writer = await asyncio.open_unix_connection(path)
            except OSError as exc:
                click.echo(f"Error: could not send that line: {exc}", err=True)
                continue
            try:
                payload = {"cmd": "say", "id": agent_id, "text": text}
                say_writer.write((json.dumps(payload) + "\n").encode())
                await say_writer.drain()
                reply = await say_reader.readline()
            finally:
                say_writer.close()
            if reply:
                error = json.loads(reply).get("error")
                if error:
                    click.echo(f"Error: {error}", err=True)

    # The event stream decides when attach ends, not stdin. Closed stdin is
    # normal — a piped or redirected `mael agent attach` is a read-only view of
    # a live agent — so `forward` finishing must not tear the stream down.
    streaming = asyncio.create_task(_stream(reader))
    typing = asyncio.create_task(forward())
    try:
        await streaming
    finally:
        typing.cancel()
        writer.close()
