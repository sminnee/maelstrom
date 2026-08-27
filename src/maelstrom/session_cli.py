"""Session-tracking CLI: `mael session record`, `mael session list`, and `mael session-channel`."""

import json
import os
import sys
from pathlib import Path

import click

from . import session_discovery, session_view
from .context import resolve_context
from .env import stop_sessions
from .session_store import (
    liveness_check as _liveness_check,
)
from .session_store import (
    read_session_file as _read_session_file,
)
from .session_store import (
    sessions_dir as _sessions_dir,
)
from .shell import exec_cmd
from .table import draw_table
from .task_cli import open_index
from .task_index import SqliteTaskIndex
from .task_store import GitFileStore
from .util import atomic_write_json, now_iso


def _find_session_file(
    session_id: str | None,
    cwd: str | None,
    pid: int | None,
) -> Path | None:
    """Find the session file matching session_id, falling back to cwd+pid."""
    sdir = _sessions_dir()
    if not sdir.is_dir():
        return None

    candidates: list[tuple[Path, dict]] = []
    for f in sdir.glob("*.json"):
        data = _read_session_file(f)
        if data is None:
            continue
        candidates.append((f, data))

    if session_id:
        for f, data in candidates:
            if data.get("session_id") == session_id:
                return f
            if data.get("session_key") == session_id:
                return f

    if cwd and pid is not None:
        for f, data in candidates:
            if data.get("cwd") == cwd and data.get("pid") == pid:
                return f

    if cwd:
        for f, data in candidates:
            if data.get("cwd") == cwd:
                return f

    return None


# --- session-channel launcher ---


@click.command("session-channel")
def session_channel() -> None:
    """Launch the Bun-based session-tracking MCP channel.

    Invoked by Claude Code via the user-wide MCP server entry installed by
    `mael install`. Not meant for humans.
    """
    module_dir = Path(__file__).parent
    repo_root = module_dir.parent.parent
    script = repo_root / "tools" / "mael-session-channel" / "index.ts"

    if not script.exists():
        click.echo(
            f"mael-session-channel script not found at {script}.\n"
            "Reinstall maelstrom from a git checkout.",
            err=True,
        )
        sys.exit(1)

    try:
        exec_cmd(["bun", "run", str(script)])
    except FileNotFoundError:
        click.echo(
            "bun is not installed or not on PATH. Install from https://bun.sh.",
            err=True,
        )
        sys.exit(127)


# --- session group ---


@click.group("session")
def session() -> None:
    """Inspect and update Claude Code session state."""


# Each hook is installed with its own `event` argument; the argument maps
# directly to a session state, or to the special `session-end` action.
#
# This keeps the record command stateless: it doesn't need to know which
# Claude Code hook fired or interpret payload fields — the hook installer
# in claude_integration.py picks the right argument per matcher.
_EVENT_TO_STATE: dict[str, str] = {
    "user-prompt-submit": "processing",
    "stop": "idle",
    "stop-failure": "idle",
    "permission-prompt": "awaiting-permission",
    "elicitation-prompt": "awaiting-permission",
    "idle-prompt": "idle",
    "ask-user-pre": "awaiting-user-input",
    "ask-user-post": "processing",
}

SESSION_END_EVENT = "session-end"
HEARTBEAT_EVENT = "heartbeat"


def _close_task_for_session(cwd: str | None) -> None:
    """Mark the launching task ``done`` when its agent session ends.

    The open session *is* the "in-progress" signal: `mael task run` exports
    ``MAEL_TASK_ID`` into the launched Claude process, and Claude Code fires
    hooks as child processes, so the `session-end` hook inherits that env var.
    Reading it here is what lets us close the task without the agent having to
    remember to run `mael task status done`.

    Defensive throughout: a non-task session (no ``MAEL_TASK_ID``) is a clean
    no-op, and any failure — unresolvable project, missing task store, task
    already gone — is swallowed so session teardown always completes. We only
    move tasks that are still ``in-progress``; a task already moved to
    ``done``/``cancelled``/``blocked`` (by the agent or the user) is left alone.
    """
    task_id = os.environ.get("MAEL_TASK_ID")
    if not task_id:
        return

    try:
        from maelstrom import task as model
        from maelstrom import task_actions
        from maelstrom.task_store import GitFileStore

        ctx = resolve_context(
            None,
            require_project=True,
            cwd=Path(cwd) if cwd else None,
        )
        project = ctx.project
        if not project:
            return  # require_project guarantees this, but narrows the type

        store = GitFileStore()
        # No index/HEAD threaded on the session-end path — scan the store directly.
        key = model.find_key(store, project, task_id, no_index=True)
        if key is None:
            return  # task already deleted — nothing to close
        if model.status_from_key(key) != model.STATUS_IN_PROGRESS:
            return  # already terminal or back in todo — don't clobber
        task_actions.move_with_actions(store, project, task_id, model.STATUS_DONE)
        click.echo(
            f"Session ended: closed task {project}/{task_id} -> {model.STATUS_DONE}",
            err=True,
        )
    except Exception:
        # A hook must never crash session teardown.
        pass


@session.command("record")
@click.argument("event")
def session_record(event: str) -> None:
    """Update session state from a Claude Code hook event.

    Reads the hook payload as JSON from stdin and rewrites the
    `state` and `updated_at` fields on the matching session file.
    """
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}

    if event not in {SESSION_END_EVENT, HEARTBEAT_EVENT, *_EVENT_TO_STATE}:
        click.echo(f"Unknown event: {event}", err=True)
        sys.exit(2)

    session_id = payload.get("session_id")
    cwd = payload.get("cwd")
    pid = payload.get("pid")

    path = _find_session_file(session_id, cwd, pid if isinstance(pid, int) else None)
    if path is None:
        return

    if event == SESSION_END_EVENT:
        # The ending session is the completion signal for the task it launched:
        # close that task before tearing down the session file.
        _close_task_for_session(cwd)
        try:
            path.unlink()
        except OSError:
            pass
        return

    data = _read_session_file(path)
    if data is None:
        return

    # heartbeat events bump updated_at without changing state, so they can
    # safely fire alongside state-setting hooks regardless of ordering.
    if event != HEARTBEAT_EVENT:
        data["state"] = _EVENT_TO_STATE[event]
    data["updated_at"] = now_iso()
    atomic_write_json(path, data)


def _derive_project_worktree(cwd: str | None) -> tuple[str | None, str | None]:
    if not cwd:
        return (None, None)
    try:
        ctx = resolve_context(
            None,
            require_project=False,
            require_worktree=False,
            cwd=Path(cwd),
        )
    except ValueError:
        return (None, None)
    return (ctx.project, ctx.worktree)


def _scan_registry() -> list[dict]:
    """Read the session registry once: GC dead/corrupt files, return live ones.

    The registry (``~/.maelstrom/sessions/*.json``) is no longer the liveness
    authority — live processes are — but its files still accumulate and still
    carry the Claude hook ``state``/``started_at`` used to enrich the listing.
    We read each file once (one ``liveness_check`` per file), unlink the corrupt
    and dead-port ones as a best-effort side pass, and hand back the live
    entries for enrichment. Doing both in one scan avoids a second connect per
    port, which the caller relies on.
    """
    sdir = _sessions_dir()
    if not sdir.is_dir():
        return []
    live: list[dict] = []
    for f in sorted(sdir.glob("*.json")):
        data = _read_session_file(f)
        if data is None or not _liveness_check(data.get("channel_port", 0)):
            try:
                f.unlink()
            except OSError:
                pass
            continue
        live.append(data)
    return live


def _task_index() -> SqliteTaskIndex:
    """The on-disk task metadata index living beside the task store.

    Opened via the task CLI's public :func:`~maelstrom.task_cli.open_index`, so
    the reverse session-id → task lookup reads the exact cache the task CLI keeps
    current — no duplicated ``index.db`` path literal.
    """
    return open_index(GitFileStore())


ID_PREFIX_LEN = 8


def _build_row(
    sess: session_discovery.LiveSession,
    registry: list[dict],
    index: SqliteTaskIndex,
) -> dict:
    """One session's display fields, with this layer's I/O injected.

    :func:`maelstrom.session_view.build_session_row` holds the logic and stays
    pure. Resolving a cwd to a project reads config and walks the filesystem, so
    that resolver is supplied here, in the layer allowed to do I/O.
    """
    return session_view.build_session_row(
        sess, registry, index, _derive_project_worktree
    )


@session.command("list")
def session_list() -> None:
    """List active Claude Code sessions.

    Live sessions come from running ``claude`` processes and their cwd (the
    same source ``mael list`` / ``task reconcile`` use), so the list is accurate
    even when the registry is stale. Each row is built by
    :func:`build_session_row`, which also feeds ``session info``. STATE and AGE
    are registry-only fields, so they are blank when nothing matches. TASK is an
    indexed reverse lookup of the session's ``--session-id``, left blank for a
    non-``mael`` ``claude``. ID is the first characters of that session-id — the
    handle ``session info`` and ``session end`` take. The registry directory is
    GC'd in the same single scan.
    """
    registry = _scan_registry()
    sessions = session_discovery.all_live_sessions()
    index = _task_index()

    rows = []
    for sess in sessions:
        row = _build_row(sess, registry, index)
        pw = (
            f"{row['project']}/{row['worktree']}"
            if row["project"] and row["worktree"]
            else row["project"]
        )
        rows.append(
            {
                "STATE": row["state"],
                "ID": row["id"][:ID_PREFIX_LEN],
                "PROJECT/WORKTREE": pw,
                "TASK": row["task"],
                "CWD": row["cwd"],
                "AGE": row["age"],
                "PID": str(row["pid"]),
            }
        )

    if not rows:
        click.echo("No active Claude Code sessions.")
        return

    rows.sort(key=lambda r: (r["PROJECT/WORKTREE"], r["PID"]))
    draw_table(rows, ["STATE", "ID", "PROJECT/WORKTREE", "TASK", "CWD", "AGE", "PID"])


def _session_handles(id: str | None) -> list[str]:
    """The handles to try, in order, for ``id`` or for the current session.

    An explicit argument is the only candidate — a named session that does not
    exist is an error, never a silent fall back to some other session.

    Without one, the candidates are the two ids a running session knows about
    itself, most precise first:

    - ``CLAUDE_CODE_SESSION_ID`` — the id of the conversation happening *now*.
      A ``/clear`` starts a new conversation and moves it.
    - ``CLAUDE_PID`` — the pid, which always resolves.

    Both are tried because the live id usually does *not* match a swept session:
    the command line holds the id the session launched with. So the pid is what
    resolves a session that has run ``/clear``.

    ``MAEL_TASK_SESSION_ID`` is deliberately not consulted. It is a task key, not
    a live-session reference: it holds the id the task was launched with, which is
    correct until a ``/clear`` and points at a dead transcript after one.
    """
    if id:
        return [id]
    found = [
        os.environ.get("CLAUDE_CODE_SESSION_ID"),
        os.environ.get("CLAUDE_PID"),
    ]
    return [h for h in found if h]


def _find_session(id: str | None) -> session_discovery.LiveSession:
    """Resolve ``id`` (or the current session) to one live session.

    Tries each handle :func:`_session_handles` gives, and returns the first that
    resolves. The CLI layer is where a model-layer ``KeyError``/``ValueError``
    becomes a ``ClickException``. An ambiguous prefix fails immediately rather
    than falling through: the user named something real, and picking one of the
    candidates for them would be a guess.

    A pid the sweep does not know resolves through
    :func:`~maelstrom.session_discovery.session_for_pid`, which reads the process
    itself. Without it a session whose ``pgrep`` sweep misses it — its own,
    often — could not name itself.
    """
    handles = _session_handles(id)
    if not handles:
        raise click.ClickException(
            "No session id given, and neither CLAUDE_CODE_SESSION_ID nor "
            "CLAUDE_PID is set."
        )

    live = session_discovery.LiveSessionSet()
    for handle in handles:
        try:
            return live.resolve(handle)
        except ValueError as e:
            raise click.ClickException(str(e))
        except KeyError:
            if handle.isdigit():
                found = session_discovery.session_for_pid(int(handle))
                if found is not None:
                    return found
            continue
    raise click.ClickException(f"No live session matching '{handles[0]}'")


@session.command("info")
@click.argument("id", required=False)
@click.pass_context
def session_info(ctx, id: str | None) -> None:
    """Show the fields of one live session.

    ID is a session id, a unique prefix of one, or a pid — the ID and PID columns
    of ``mael session list``. Without it, the session you run this in is used.

    ``mael --json session info`` prints the same fields as JSON. The text form
    omits a field with nothing to report; the JSON form always carries every key,
    so a script can rely on the shape.
    """
    sess = _find_session(id)
    row = _build_row(sess, _scan_registry(), _task_index())

    if ctx.obj.get("json", False) if ctx.obj else False:
        click.echo(json.dumps(row, indent=2))
        return

    click.echo(f"pid:      {row['pid']}")
    # Optional fields are omitted when blank, like `mael task show`: a bare
    # `claude` has no id and no registry entry to enrich it from.
    if row["id"]:
        click.echo(f"id:       {row['id']}")
    if row["state"]:
        click.echo(f"state:    {row['state']}")
    if row["project"]:
        click.echo(f"project:  {row['project']}")
    if row["worktree"]:
        click.echo(f"worktree: {row['worktree']}")
    if row["task"]:
        click.echo(f"task:     {row['task']}")
    click.echo(f"cwd:      {row['cwd']}")
    if row["age"]:
        click.echo(f"age:      {row['age']}")
    if row["model"]:
        click.echo(f"model:    {row['model']}")


@session.command("end")
@click.argument("id", required=False)
def session_end(id: str | None) -> None:
    """Stop a live session, leaving its worktree in place.

    ID takes the same forms as ``mael session info``. Without it, the session you
    run this in is stopped: ``mael`` is a child of that session, so it signals its
    parent and exits with it. The ended session is resumable — its transcript is
    complete and ``claude --resume`` opens it again.

    The stop is graceful and can take up to 15 seconds: SIGINT to let a busy
    session wind down, then SIGTERM to any survivor, never SIGKILL. This does not
    close the task the session was launched for. The Claude ``session-end`` hook
    still fires on shutdown and closes it.
    """
    sess = _find_session(id)

    # Refuse to signal the `mael` process itself. Only a handle naming `mael`
    # directly reaches here; the enclosing session resolves to the parent pid.
    # Saying so beats silence: an empty run and a crash look identical otherwise.
    if sess.pid == os.getpid():
        click.echo(f"claude session (pid {sess.pid}) is this session; not stopping it.")
        return

    for msg in stop_sessions([sess]):
        click.echo(msg)
