"""Session CLI: `mael session list`, `mael session info`, `mael session end`.

A session here is a running ``claude`` process. Everything shown comes from the
process itself (via :mod:`maelstrom.session_discovery`) plus the task index's
reverse lookup on the session id. There is no registry file to consult: the
session-tracking channel that wrote one is gone, and ``mael agent list`` is
where a driven agent's state lives.
"""

import asyncio
import json
import os
from pathlib import Path

import click

from . import session_discovery
from .context import resolve_context
from .env import stop_sessions
from .table import draw_table
from .task_cli import open_index
from .task_index import SqliteTaskIndex
from .task_store import GitFileStore


@click.group("session")
def session() -> None:
    """Inspect and stop Claude Code sessions."""


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


def _task_index() -> SqliteTaskIndex:
    """The on-disk task metadata index living beside the task store.

    Opened via the task CLI's public :func:`~maelstrom.task_cli.open_index`, so
    the reverse session-id → task lookup reads the exact cache the task CLI keeps
    current — no duplicated ``index.db`` path literal.
    """
    return open_index(GitFileStore())


ID_PREFIX_LEN = 8


def build_session_row(
    sess: session_discovery.LiveSession,
    index: SqliteTaskIndex,
) -> dict:
    """Everything ``mael session`` knows about one live session, as a flat dict.

    Both ``session list`` and ``session info`` render from the result, and
    ``mael --json session info`` emits it as-is.

    ``pid`` and ``cwd`` come from the process itself and are always right.
    ``task`` is an indexed reverse lookup on the session id, blank for a bare
    ``claude`` that ``mael`` did not launch. Every key is always present; a field
    with nothing to report is an empty string.
    """
    cwd = str(sess.cwd)
    project, worktree = _derive_project_worktree(cwd)

    task_id = ""
    if sess.session_id:
        meta = index.find_by_session_id(sess.session_id)
        if meta is not None:
            task_id = meta.id

    return {
        "id": sess.session_id or "",
        "pid": sess.pid,
        "project": project or "",
        "worktree": worktree or "",
        "task": task_id,
        "cwd": cwd,
    }


@session.command("list")
def session_list() -> None:
    """List running Claude Code sessions.

    Sessions come from running ``claude`` processes and their cwd — the same
    source ``mael list`` and ``task reconcile`` use. TASK is an indexed reverse
    lookup of the session's ``--session-id``, left blank for a ``claude`` that
    ``mael`` did not launch. ID is the first characters of that session-id — the
    handle ``session info`` and ``session end`` take.

    What an agent is *doing* is not here: a driven agent reports that to the
    daemon, so ``mael agent list`` shows it, including what a waiting one waits
    on.
    """
    sessions = asyncio.run(session_discovery.all_live_sessions())
    index = _task_index()

    rows = []
    for sess in sessions:
        row = build_session_row(sess, index)
        pw = (
            f"{row['project']}/{row['worktree']}"
            if row["project"] and row["worktree"]
            else row["project"]
        )
        rows.append(
            {
                "ID": row["id"][:ID_PREFIX_LEN],
                "PROJECT/WORKTREE": pw,
                "TASK": row["task"],
                "CWD": row["cwd"],
                "PID": str(row["pid"]),
            }
        )

    if not rows:
        click.echo("No running Claude Code sessions.")
        return

    rows.sort(key=lambda r: (r["PROJECT/WORKTREE"], r["PID"]))
    draw_table(rows, ["ID", "PROJECT/WORKTREE", "TASK", "CWD", "PID"])


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
                found = asyncio.run(session_discovery.session_for_pid(int(handle)))
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
    row = build_session_row(sess, _task_index())

    if ctx.obj.get("json", False) if ctx.obj else False:
        click.echo(json.dumps(row, indent=2))
        return

    click.echo(f"pid:      {row['pid']}")
    # Optional fields are omitted when blank, like `mael task show`: a bare
    # `claude` has no session id and no task to name.
    if row["id"]:
        click.echo(f"id:       {row['id']}")
    if row["project"]:
        click.echo(f"project:  {row['project']}")
    if row["worktree"]:
        click.echo(f"worktree: {row['worktree']}")
    if row["task"]:
        click.echo(f"task:     {row['task']}")
    click.echo(f"cwd:      {row['cwd']}")


@session.command("end")
@click.argument("id", required=False)
def session_end(id: str | None) -> None:
    """Stop a live session, leaving its worktree in place.

    ID takes the same forms as ``mael session info``. Without it, the session you
    run this in is stopped: ``mael`` is a child of that session, so it signals its
    parent and exits with it. The ended session is resumable — its transcript is
    complete and ``claude --resume`` opens it again.

    The stop is graceful and can take up to 15 seconds: SIGINT to let a busy
    session wind down, then SIGTERM to any survivor, never SIGKILL.

    This does not close the task the session was launched for. Close it with
    ``mael task status done``; ``mael task reconcile`` finds one left behind.
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
