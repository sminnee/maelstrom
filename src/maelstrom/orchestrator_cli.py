"""``mael orchestrator`` — run the orchestrator server.

The thin CLI over :mod:`maelstrom.orchestrator.server`. It wires the real
sources — the task notebook, ``list-all`` and the agent host's socket — into
an :class:`~maelstrom.orchestrator.server.Orchestrator` and serves it. See
``docs/dev/orchestrator-server.md``.
"""

import asyncio
import sys
from concurrent.futures import Executor, ThreadPoolExecutor
from pathlib import Path

import click

from .agent_transport import SocketAsyncDaemonClient, daemon_paths
from .context import load_global_config
from .desk_store import JsonDeskStore
from .orchestrator.routes import build_app, serve_app
from .orchestrator.server import Orchestrator
from .orchestrator.sources import (
    CloseBlocked,
    ListAllWorktreeSource,
    NotebookTaskSource,
)
from .task_cli import open_index
from .task_launch import LaunchBlocked
from .task_store import GitFileStore
from .worktree import WorktreeSetup, find_all_projects, setup_worktree_for_branch
from .worktree_close import close_worktree_fully
from .worktree_model import WorktreeError

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def build_orchestrator(*, executor: Executor | None = None) -> Orchestrator:
    """An orchestrator over the real notebook, ``list-all`` and agent host.

    The agent host is the daemon this environment names in ``MAEL_AGENT_ROOT``,
    so the orchestrator a worktree runs talks to that worktree's daemon.
    ``executor`` runs the blocking reads; :func:`run_server` passes a pool of
    one thread, because the SQLite index behind the notebook is bound to the
    thread that first opens it.
    """
    projects_dir = load_global_config().projects_dir
    store = GitFileStore()

    def open_worktree(project: str, branch: str, base: str) -> WorktreeSetup:
        # The launcher owns install; ``base`` seeds the branch's stored base
        # the first time, as ``mael task run`` does.
        #
        # A worktree that cannot be opened is a refused launch, not a crashed
        # server: LaunchBlocked is the wire's word for "this task could not
        # start, and here is why", so the domain errors are converted to it.
        try:
            return setup_worktree_for_branch(
                projects_dir / project,
                project,
                branch,
                run_install=False,
                base=base or None,
                announce=lambda line: click.echo(line, err=True),
            )
        except (ValueError, WorktreeError) as exc:
            raise LaunchBlocked(str(exc)) from exc

    def close_worktree(project: str, nato: str, path: str) -> None:
        # Never forced: unmerged work is refused, and the model's own message
        # is what the button shows. Forcing writes a wip commit and a reopen
        # task, which is too much for one click — ``mael close --force`` does it.
        outcome = close_worktree_fully(
            project, nato, Path(path), projects_dir / project, force=False
        )
        if not outcome.close.success:
            raise CloseBlocked(outcome.close.message)

    tasks = NotebookTaskSource(
        store,
        lambda: [path.name for path in find_all_projects(projects_dir)],
        index=open_index(store),
        open_worktree=open_worktree,
    )
    worktrees = ListAllWorktreeSource(projects_dir, close=close_worktree)
    daemon = SocketAsyncDaemonClient(str(daemon_paths().socket))
    return Orchestrator(
        tasks, worktrees, daemon, desk=JsonDeskStore(), executor=executor
    )


def run_server(host: str, port: int) -> None:
    """Build the orchestrator and serve it until interrupted.

    The worker pool lives for the serve call, so an interrupt does not wait on
    a read in flight past the point the server has stopped.
    """
    with ThreadPoolExecutor(max_workers=1) as executor:
        orchestrator = build_orchestrator(executor=executor)
        asyncio.run(serve_app(build_app(orchestrator), host, port))


@click.group()
def orchestrator() -> None:
    """Serve the world to the orchestrator UI."""


@orchestrator.command("serve")
@click.option("--host", default=DEFAULT_HOST, show_default=True, help="Bind address.")
@click.option(
    "--port", default=DEFAULT_PORT, show_default=True, type=int, help="Bind port."
)
def cmd_serve(host: str, port: int) -> None:
    """Run the orchestrator server in the foreground.

    The agent host is the daemon ``MAEL_AGENT_ROOT`` names, so a worktree's
    orchestrator talks to that worktree's daemon.
    """
    click.echo(f"Serving on http://{host}:{port}", err=True)
    try:
        run_server(host, port)
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
