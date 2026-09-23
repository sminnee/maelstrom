"""``mael-agent-daemon`` — run the agent daemon, and read its records.

``serve`` runs the daemon, and ``status``, ``reconcile``, ``gc`` and ``list``
read its records against the process table. It is a CLI root of its own, not a
``mael`` group, so the ``mael`` CLI imports nothing of the daemon.
"""

import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import click

from mael_agent.agent_transport import (
    KIND_UNREACHABLE,
    AsyncDaemonClient,
    DaemonPaths,
    RootUnset,
    all_roots,
    daemon_paths,
    require_root,
)
from mael_agent.agent_transport import client as daemon_client
from mael_common.cli_async import AsyncGroup
from mael_common.process_table import ProcessTableUnavailable, list_claude_processes
from mael_common.table import format_table
from mael_common.util import format_uptime, get_maelstrom_dir

from . import agent_server
from .agent_reconcile import (
    DAEMON_LIST_COLUMNS,
    UNKNOWN,
    Verdict,
    build_daemon_list_rows,
    reconcile,
)
from .agent_server import AgentDaemon, apply_reconciliation, kill_groups
from .agent_spec_store import JsonAgentSpecStore


def _daemon_at(paths: DaemonPaths) -> AsyncDaemonClient:
    """A client for one daemon root. Through ``client_factory``, so a test fake
    intercepts it."""
    return daemon_client(socket_path=str(paths.socket))


@click.group("mael-agent-daemon", cls=AsyncGroup)
def cli() -> None:
    """Inspect the agent daemon, and run one as a service.

    The environment manager owns a daemon's lifetime: `mael self-env start`
    runs the everyday daemon, and `mael env start` runs this worktree's. Both
    run `serve` as a service, so `mael self-env restart agent-daemon` is how a
    daemon picks up new code.

    `status` says which daemon is answering and whose code it runs, which is
    the question a long-lived daemon makes worth asking.
    """


@cli.command("serve")
async def cmd_daemon_serve() -> None:
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
        # Ctrl-C is not caught here: asyncio re-raises it from the loop, which
        # `cli_async` owns and ends cleanly on. See its `_run`.
        await daemon.serve()
    except RuntimeError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.command("status")
async def cmd_daemon_status() -> None:
    """Say which daemon is serving this root, and what code it runs.

    One root can be served by a daemon spawned from any worktree, and it
    holds the modules it imported at start. So the source tree and the start
    time are the two fields worth reading here.
    """
    paths = daemon_paths()
    # No skew warning: the serving tree is what this command was asked for, so
    # it belongs in the `source` row rather than in a warning printed
    # immediately above it.
    reply = await _daemon_at(paths).request({"cmd": "ping"})
    if "error" in reply:
        # A daemon predating `ping` falls through to the agent lookup and
        # answers "no such agent", which reads here as a fault in this command
        # rather than in the daemon it is asking. Say what it means instead.
        error = reply["error"]
        if reply.get("kind"):
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


async def _reconcile_root(paths: DaemonPaths, *, act: bool) -> _RootReport:
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
    reply = await client.request({"cmd": "gc" if act else "reconcile"})
    if "error" not in reply:
        rows = (await client.request({"cmd": "list"})).get("agents", [])
        held = {row["id"] for row in rows if not row.get("parent")}
        verdicts = [Verdict(**v) for v in reply.get("verdicts", [])]
        return _RootReport(paths, verdicts, list(reply.get("killed", [])), held, True)
    # Only an absent daemon licenses the spec-file fallback below. A denial
    # means a daemon is probably still holding these agents, so killing the
    # strays it reports would kill live ones.
    if reply.get("kind") != KIND_UNREACHABLE:
        raise click.ClickException(reply["error"])
    specs = JsonAgentSpecStore(paths.spec_dir)
    try:
        processes = await list_claude_processes()
    except ProcessTableUnavailable as exc:
        raise click.ClickException(f"the process table is unavailable: {exc}") from exc
    result = reconcile(specs.list(), processes, set(), resume_strays=False)
    killed = apply_reconciliation(result, specs, _kill_group()) if act else []
    return _RootReport(paths, list(result.verdicts), killed, set(), False)


def _kill_group():
    """The group kill, read at call time so a test's patch of the seam reaches it."""
    return agent_server.kill_group


def _roots(every: bool) -> list[DaemonPaths]:
    if every:
        return all_roots(get_maelstrom_dir())
    return [daemon_paths()]


async def _reports(every: bool, *, act: bool) -> list[_RootReport]:
    """Reconcile the chosen roots. With every root, a process unknown to all of
    them has no owner anywhere and is killed too, when acting."""
    reports = [await _reconcile_root(paths, act=act) for paths in _roots(every)]
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


@cli.command("reconcile")
@all_roots_option
@json_option
async def cmd_daemon_reconcile(all_roots: bool, as_json: bool) -> None:
    """Say what `gc` would do, doing nothing.

    Each spawn record against the process table: owned, stray, duplicate,
    resumable, crashed or superseded, and any driven `claude` no record here
    names. Asks the daemon when one answers, else reads the records and the
    table itself.
    """
    reports = await _reports(all_roots, act=False)
    if as_json:
        _emit_json(reports, acted=False)
        return
    _print_verdicts(reports, acted=False)


@cli.command("gc")
@all_roots_option
@json_option
async def cmd_daemon_gc(all_roots: bool, as_json: bool) -> None:
    """Kill the strays and duplicates, and write off the crashed records.

    Never resumes: a stray's record stays `running`, so the next daemon start
    brings the agent back exactly once. Under `--all-roots`, a driven `claude`
    no root's records name is killed too; from one root it is only reported,
    because it may belong to another.
    """
    reports = await _reports(all_roots, act=True)
    if as_json:
        _emit_json(reports, acted=True)
        return
    _print_verdicts(reports, acted=True)


@cli.command("list")
@all_roots_option
@json_option
async def cmd_daemon_list(all_roots: bool, as_json: bool) -> None:
    """Every spawn record, with its pid, whether that pid is alive, and whether
    the daemon holds it — so a mismatch is read off the table, not inferred.
    """
    reports = await _reports(all_roots, act=False)
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
    click.echo(format_table(rows, columns))


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


def main() -> None:
    """The ``mael-agent-daemon`` console script."""
    cli()
