"""``mael agent`` — start, watch, answer and teleport into daemon-driven agents.

The thin CLI over :mod:`maelstrom.agent_server`. Every command is one NDJSON
round-trip to the daemon's control socket, so this module holds no state and
does no agent logic: it parses flags, sends a command, and prints the reply.
Rendering goes through ``build_agent_row`` in the model layer, the way
``session_cli`` renders through ``session_view``.

No command starts a daemon. The environment manager does: `mael self-env
start` runs the everyday daemon and `mael env start` runs this worktree's.
A command that finds none says so, naming the root — see
``docs/dev/agent-daemon.md``.
"""

import asyncio
import json
import shlex
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
    build_start_payload,
)
from .agent_reconcile import (
    DAEMON_LIST_COLUMNS,
    UNKNOWN,
    Verdict,
    build_daemon_list_rows,
    reconcile,
)
from .agent_server import (
    SCOPE_ALL,
    SCOPE_RUNNING,
    SCOPE_STOPPED,
    AgentDaemon,
    apply_reconciliation,
    kill_groups,
)
from .agent_spec_store import JsonAgentSpecStore
from .agent_transport import (
    UNREACHABLE_MARKER,
    DaemonClient,
    DaemonPaths,
    RootUnset,
    SocketAsyncDaemonClient,
    all_roots,
    daemon_paths,
    require_root,
)
from .agent_transport import client as daemon_client
from .context import resolve_context
from .env import format_uptime
from .session_discovery import ProcessTableUnavailable, list_claude_processes
from .table import draw_table

#: Columns ``mael agent list`` prints, in order.
LIST_COLUMNS = [
    "id",
    "parent",
    "description",
    "state",
    "mode",
    "waiting_on",
    "last_message",
    "cwd",
    "model",
    "cost",
]

#: Columns ``mael agent show`` prints for a parent's subagents, in order.
SUBAGENT_COLUMNS = ["id", "state", "description", "last_message"]


def _daemon_at(paths: DaemonPaths | None) -> DaemonClient:
    """A client for one daemon root, or for this environment's when given none.

    Goes through ``client_factory`` either way, so a test fake still
    intercepts a command that names a root. The fake takes the socket path
    and ignores it, having no socket to reach.
    """
    socket_path = str(paths.socket) if paths is not None else None
    return daemon_client(socket_path=socket_path)


def _send(payload: dict[str, Any]) -> dict[str, Any]:
    """Send one command, printing the daemon's error and exiting on failure.

    A ``warning`` is not a failure: the command did what was asked, and
    something alongside it did not. It prints and the command still succeeds.
    """
    reply = _daemon_at(None).request(payload)
    if "error" in reply:
        click.echo(f"Error: {reply['error']}", err=True)
        sys.exit(1)
    if reply.get("warning"):
        click.echo(f"Warning: {reply['warning']}", err=True)
    return reply


@click.group()
def agent() -> None:
    """Drive Claude agents over a stream-json pipe."""


@agent.group("daemon")
def cmd_daemon() -> None:
    """Inspect the agent daemon, and run one as a service.

    The environment manager owns a daemon's lifetime: `mael self-env start`
    runs the everyday daemon, and `mael env start` runs this worktree's. Both
    run `serve` as a service, so `mael self-env restart agent-daemon` is how a
    daemon picks up new code.

    `status` says which daemon is answering and whose code it runs, which is
    the question a long-lived daemon makes worth asking.
    """


@cmd_daemon.command("serve")
def cmd_daemon_serve() -> None:
    """Run the agent daemon in the foreground, on the root this environment names.

    The root comes from ``MAEL_AGENT_ROOT`` and from nowhere else. There is no
    flag: a flag is what let a worktree's daemon be started on the everyday
    root, where it served that worktree's test code to every session on the
    machine.
    """
    try:
        root = require_root()
    except RootUnset as exc:
        raise click.UsageError(str(exc)) from exc
    daemon = AgentDaemon(root)
    try:
        asyncio.run(daemon.serve())
    except KeyboardInterrupt:
        pass
    except RuntimeError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cmd_daemon.command("status")
def cmd_daemon_status() -> None:
    """Say which daemon is serving this root, and what code it runs.

    One root can be served by a daemon spawned from any worktree, and it
    holds the modules it imported at start. So the source tree and the start
    time are the two fields worth reading here.
    """
    paths = daemon_paths()
    # No skew warning: the serving tree is what this command was asked for, so
    # it belongs in the `source` row rather than in a warning printed
    # immediately above it.
    reply = _daemon_at(paths).request({"cmd": "ping"})
    if "error" in reply:
        # A daemon predating `ping` falls through to the agent lookup and
        # answers "no such agent", which reads here as a fault in this command
        # rather than in the daemon it is asking. Say what it means instead.
        error = reply["error"]
        if UNREACHABLE_MARKER in error:
            click.echo(f"Error: {error}", err=True)
        else:
            click.echo(
                "Error: the daemon on "
                f"{paths.root} is older than this "
                "code: it does not answer `ping`.\n"
                "       Run `mael self-env restart agent-daemon` to serve from "
                "this tree.",
                err=True,
            )
        sys.exit(1)
    identity = reply["daemon"]
    rows = [
        ("root", identity.get("root", "")),
        ("socket", identity.get("socket_path", "")),
        ("pid", str(identity.get("pid", ""))),
        ("version", identity.get("version", "")),
        ("source", identity.get("source_tree", "")),
        ("specs", identity.get("spec_dir", "")),
        ("started", _started(identity.get("started_at", ""))),
        ("agents", str(identity.get("agents", 0))),
    ]
    width = max(len(label) for label, _ in rows) + 1
    for label, value in rows:
        click.echo(f"{label + ':':<{width + 1}} {value}")


#: The two flags `gc`, `list` and `reconcile` share.
all_roots_option = click.option(
    "--all-roots",
    is_flag=True,
    help="Every daemon root on this machine, not only the resolved one.",
)
json_option = click.option("--json", "as_json", is_flag=True, help="Emit JSON.")


@dataclass
class _RootReport:
    """One root's reconcile: its verdicts, what was killed, and who holds what."""

    paths: DaemonPaths
    verdicts: list[Verdict]
    killed: list[int]
    held: set[str]
    reachable: bool


def _reconcile_root(paths: DaemonPaths, *, act: bool) -> _RootReport:
    """Reconcile one root, through its daemon when one answers, else locally.

    The daemon knows which agents it holds, so its verdict is the better one.
    With no daemon there is nothing held, and the CLI reads the records and
    the process table itself — which is the case `gc` exists for: a daemon
    that died and left its children behind.

    Raises:
        click.ClickException: If the process table cannot be read, or the
            daemon answered something other than "no daemon here".
    """
    client = _daemon_at(paths)
    reply = client.request({"cmd": "gc" if act else "reconcile"})
    if "error" not in reply:
        rows = client.request({"cmd": "list"}).get("agents", [])
        held = {row["id"] for row in rows if not row.get("parent")}
        verdicts = [Verdict(**v) for v in reply.get("verdicts", [])]
        return _RootReport(paths, verdicts, list(reply.get("killed", [])), held, True)
    if UNREACHABLE_MARKER not in reply["error"]:
        raise click.ClickException(reply["error"])
    specs = JsonAgentSpecStore(paths.spec_dir)
    try:
        processes = list_claude_processes()
    except ProcessTableUnavailable as exc:
        raise click.ClickException(f"the process table is unavailable: {exc}") from exc
    result = reconcile(specs.list(), processes, set(), resume_strays=False)
    killed = apply_reconciliation(result, specs, _kill_group()) if act else []
    return _RootReport(paths, list(result.verdicts), killed, set(), False)


def _kill_group():
    """The group kill, read at call time so a test's patch of the seam reaches it."""
    from . import agent_server

    return agent_server.kill_group


def _roots(every: bool) -> list[DaemonPaths]:
    if every:
        return all_roots()
    return [daemon_paths()]


def _reports(every: bool, *, act: bool) -> list[_RootReport]:
    """Reconcile the chosen roots. With every root, a process unknown to all of
    them has no owner anywhere and is killed too, when acting."""
    reports = [_reconcile_root(paths, act=act) for paths in _roots(every)]
    if every and act:
        orphans = _unknown_everywhere(reports)
        if orphans:
            killed = kill_groups(sorted(orphans), _kill_group())
            reports[0].killed += killed
    return reports


def _unknown_everywhere(reports: list[_RootReport]) -> set[int]:
    """Group ids of driven processes no root's records claim.

    A process one root owns is `owned` or `stray` there and `unknown` to the
    rest, so only a pid that is `unknown` in every report is nobody's.
    """
    claimed: set[int] = set()
    unknown: dict[int, int] = {}
    for report in reports:
        for v in report.verdicts:
            if v.pid is None:
                continue
            if v.kind == UNKNOWN:
                unknown[v.pid] = v.pgid if v.pgid is not None else v.pid
            else:
                claimed.add(v.pid)
    return {pgid for pid, pgid in unknown.items() if pid not in claimed}


def _print_verdicts(reports: list[_RootReport], *, acted: bool) -> None:
    for report in reports:
        if len(reports) > 1:
            where = "daemon up" if report.reachable else "no daemon"
            click.echo(f"{report.paths.root} ({where}):")
        if not report.verdicts:
            click.echo("  nothing to reconcile")
        for v in report.verdicts:
            who = v.agent_id or v.session_id or "?"
            pid = f" pid {v.pid}" if v.pid is not None else ""
            reason = f" — {v.reason}" if v.reason else ""
            click.echo(f"  {v.kind:<11} {who}{pid}{reason}")
        if acted:
            if report.killed:
                click.echo(f"  killed groups: {', '.join(map(str, report.killed))}")
            else:
                click.echo("  killed nothing")


def _emit_json(reports: list[_RootReport], acted: bool) -> None:
    out = [
        {
            "root": str(r.paths.root),
            "reachable": r.reachable,
            "verdicts": [asdict(v) for v in r.verdicts],
            **({"killed": r.killed} if acted else {}),
        }
        for r in reports
    ]
    click.echo(json.dumps(out if len(out) > 1 else out[0], indent=2))


@cmd_daemon.command("reconcile")
@all_roots_option
@json_option
def cmd_daemon_reconcile(all_roots: bool, as_json: bool) -> None:
    """Say what `gc` would do, doing nothing.

    Each spawn record against the process table: owned, stray, duplicate,
    resumable, crashed or superseded, and any driven `claude` no record here
    names. Asks the daemon when one answers, else reads the records and the
    table itself.
    """
    reports = _reports(all_roots, act=False)
    if as_json:
        _emit_json(reports, acted=False)
        return
    _print_verdicts(reports, acted=False)


@cmd_daemon.command("gc")
@all_roots_option
@json_option
def cmd_daemon_gc(all_roots: bool, as_json: bool) -> None:
    """Kill the strays and duplicates, and write off the crashed records.

    Never resumes: a stray's record stays `running`, so the next daemon start
    brings the agent back exactly once. Under `--all-roots`, a driven `claude`
    no root's records name is killed too; from one root it is only reported,
    because it may belong to another.
    """
    reports = _reports(all_roots, act=True)
    if as_json:
        _emit_json(reports, acted=True)
        return
    _print_verdicts(reports, acted=True)


@cmd_daemon.command("list")
@all_roots_option
@json_option
def cmd_daemon_list(all_roots: bool, as_json: bool) -> None:
    """Every spawn record, with its pid, whether that pid is alive, and whether
    the daemon holds it — so a mismatch is read off the table, not inferred.
    """
    reports = _reports(all_roots, act=False)
    rows: list[dict[str, str]] = []
    for report in reports:
        records = JsonAgentSpecStore(report.paths.spec_dir).list()
        for row in build_daemon_list_rows(records, report.verdicts, report.held):
            rows.append({"root": str(report.paths.root), **row} if all_roots else row)
    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        click.echo("No spawn records.")
        return
    columns = (["root"] if all_roots else []) + DAEMON_LIST_COLUMNS
    draw_table(rows, columns)


def _started(stamp: str) -> str:
    """``<local time> (<age> ago)``, or the raw stamp when it will not parse.

    The age is the useful half: a daemon started days ago is the one holding
    stale code. ``format_uptime`` is the same renderer ``mael env status``
    uses, so an age reads the same everywhere.
    """
    if not stamp:
        return ""
    try:
        started = datetime.fromisoformat(stamp)
    except ValueError:
        return stamp
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    local = started.astimezone().strftime("%Y-%m-%d %H:%M")
    return f"{local} ({format_uptime(started.isoformat())} ago)"


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
        build_start_payload(
            Path(cwd).resolve(),
            prompt=prompt,
            permission_mode=mode,
            model=model,
            session_id=session_id,
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
def cmd_list(
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
def cmd_say(agent_id: str, text: str) -> None:
    """Send TEXT to an agent as a user message."""
    _send({"cmd": "say", "id": agent_id, "text": text})


@agent.command("run")
@click.argument("agent_id")
@click.argument("command")
def cmd_run(agent_id: str, command: str) -> None:
    """Run COMMAND in the agent's directory and give it the output.

    The same thing a `!` line does in the Claude Code terminal: the host runs
    it, and the command and its output become context. Maelstrom asks the agent
    for nothing, though the agent often remarks on what it read.
    """
    _send({"cmd": "run", "id": agent_id, "command": command})


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
