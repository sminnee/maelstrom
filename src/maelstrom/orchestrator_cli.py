"""``mael orchestrator`` — run the orchestrator server.

The thin CLI over :mod:`maelstrom.orchestrator.server`. It wires the real
sources — the task notebook, ``list-all`` and the agent host's socket — into
an :class:`~maelstrom.orchestrator.server.Orchestrator` and serves it. See
``docs/dev/orchestrator-server.md``.
"""

import asyncio
import sys
from concurrent.futures import Executor, ThreadPoolExecutor

import click

from .agent_transport import resolve_socket_path
from .context import load_global_config
from .desk_store import JsonDeskStore
from .orchestrator.daemon_bridge import SocketAsyncDaemonClient
from .orchestrator.server import Orchestrator
from .orchestrator.sources import ListAllWorktreeSource, NotebookTaskSource
from .task import Task
from .task_cli import open_index
from .task_store import GitFileStore
from .worktree import WorktreeSetup, find_all_projects, setup_worktree_for_branch

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def build_orchestrator(
    socket_path: str | None = None, *, executor: Executor | None = None
) -> Orchestrator:
    """An orchestrator over the real notebook, ``list-all`` and agent host.

    ``executor`` runs the blocking reads; :func:`run_server` passes a pool of
    one thread, because the SQLite index behind the notebook is bound to the
    thread that first opens it.
    """
    projects_dir = load_global_config().projects_dir
    store = GitFileStore()

    def open_worktree(project: str, task: Task, branch: str) -> WorktreeSetup:
        # The launcher owns install; ``task.base`` seeds the branch's stored
        # base the first time, as ``mael task run`` does.
        return setup_worktree_for_branch(
            projects_dir / project,
            project,
            branch,
            run_install=False,
            base=task.base or None,
            announce=lambda line: click.echo(line, err=True),
        )

    tasks = NotebookTaskSource(
        store,
        lambda: [path.name for path in find_all_projects(projects_dir)],
        index=open_index(store),
        open_worktree=open_worktree,
    )
    worktrees = ListAllWorktreeSource(projects_dir)
    daemon = SocketAsyncDaemonClient(socket_path or resolve_socket_path())
    return Orchestrator(
        tasks, worktrees, daemon, desk=JsonDeskStore(), executor=executor
    )


def run_server(host: str, port: int, socket_path: str | None) -> None:
    """Build the orchestrator and serve it until interrupted.

    The worker pool lives for the serve call, so an interrupt does not wait on
    a read in flight past the point the server has stopped.
    """
    with ThreadPoolExecutor(max_workers=1) as executor:
        orchestrator = build_orchestrator(socket_path, executor=executor)
        asyncio.run(orchestrator.serve(host, port))


@click.group()
def orchestrator() -> None:
    """Serve the world to the orchestrator UI."""


@orchestrator.command("serve")
@click.option("--host", default=DEFAULT_HOST, show_default=True, help="Bind address.")
@click.option(
    "--port", default=DEFAULT_PORT, show_default=True, type=int, help="Bind port."
)
@click.option(
    "--socket", "socket_path", default=None, help="Agent host control socket."
)
def cmd_serve(host: str, port: int, socket_path: str | None) -> None:
    """Run the orchestrator server in the foreground."""
    click.echo(f"Serving on ws://{host}:{port}", err=True)
    try:
        run_server(host, port, socket_path)
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
