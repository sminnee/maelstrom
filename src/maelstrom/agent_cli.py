"""``mael agent`` — start, watch, answer and teleport into daemon-driven agents.

The thin CLI over :mod:`maelstrom.agent_server`. Every command is one NDJSON
round-trip to the daemon's control socket, so this module holds no state and
does no agent logic: it parses flags, sends a command, and prints the reply.
Rendering goes through ``build_agent_row`` in the model layer, the way
``session_cli`` renders through ``session_view``.

``mael agent daemon`` runs the daemon in the foreground. Nothing starts it
automatically — see ``docs/dev/agent-daemon.md``.
"""

import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from .agent_server import AgentDaemon
from .agent_transport import DaemonClient, SocketDaemonClient, resolve_socket_path
from .table import draw_table

#: Columns ``mael agent list`` prints, in order.
LIST_COLUMNS = ["id", "state", "waiting_on", "cwd", "model", "cost"]


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
    help="Pin the agent to this Claude session id, so it can be resumed.",
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
    try:
        reader, writer = await asyncio.open_unix_connection(path)
    except OSError as exc:
        click.echo(f"Error: agent daemon not reachable at {path}: {exc}", err=True)
        sys.exit(1)

    writer.write((json.dumps({"cmd": "attach", "id": agent_id}) + "\n").encode())
    await writer.drain()

    async def show() -> None:
        while True:
            line = await reader.readline()
            if not line:
                return
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "error" in event and "type" not in event:
                click.echo(f"Error: {event['error']}", err=True)
                return
            text = _render(event)
            if text:
                click.echo(text)

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
    streaming = asyncio.create_task(show())
    typing = asyncio.create_task(forward())
    try:
        await streaming
    finally:
        typing.cancel()
        writer.close()
