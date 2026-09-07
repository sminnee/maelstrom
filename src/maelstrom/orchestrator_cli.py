"""``mael orchestrator`` — run the orchestrator server.

The thin CLI over :mod:`maelstrom.orchestrator.server`. It wires the real
sources — the task notebook, ``list-all`` and the agent host's socket — into
an :class:`~maelstrom.orchestrator.server.Orchestrator` and serves it. See
``docs/dev/orchestrator-server.md``.
"""

import asyncio
import logging
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


#: What every log line looks like. The time and the level come first, because
#: the question asked of this file is always "what happened, and when".
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

#: Levels a reader may ask for, loudest last.
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

DEFAULT_LOG_LEVEL = "INFO"


def setup_logging(level: str = DEFAULT_LOG_LEVEL) -> None:
    """Send timestamped logs to stderr, which ``mael env`` captures to a file.

    Configured here rather than in :func:`build_app`, because the test suite
    runs the real app and must not inherit a global logging setup.

    ``aiohttp.access`` is left at WARNING on purpose: it is a live default that
    would otherwise write a line per SSE ping once a handler exists at INFO.
    """
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format=LOG_FORMAT,
        stream=sys.stderr,
        force=True,
    )
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


def _log_unhandled(_loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Log what a task died of when nobody was awaiting it to find out."""
    logging.getLogger("maelstrom.orchestrator").error(
        "unhandled error in the event loop: %s",
        context.get("message", "(no message)"),
        exc_info=context.get("exception"),
    )


def run_server(host: str, port: int, log_level: str = DEFAULT_LOG_LEVEL) -> None:
    """Build the orchestrator and serve it until interrupted.

    The worker pool lives for the serve call, so an interrupt does not wait on
    a read in flight past the point the server has stopped.
    """
    setup_logging(log_level)

    async def serve() -> None:
        asyncio.get_running_loop().set_exception_handler(_log_unhandled)
        await serve_app(build_app(orchestrator), host, port)

    with ThreadPoolExecutor(max_workers=1) as executor:
        orchestrator = build_orchestrator(executor=executor)
        asyncio.run(serve())


@click.group()
def orchestrator() -> None:
    """Serve the world to the orchestrator UI."""


@orchestrator.command("serve")
@click.option("--host", default=DEFAULT_HOST, show_default=True, help="Bind address.")
@click.option(
    "--port", default=DEFAULT_PORT, show_default=True, type=int, help="Bind port."
)
@click.option(
    "--log-level",
    default=DEFAULT_LOG_LEVEL,
    show_default=True,
    type=click.Choice(LOG_LEVELS, case_sensitive=False),
    help="How much to log.",
)
def cmd_serve(host: str, port: int, log_level: str) -> None:
    """Run the orchestrator server in the foreground.

    The agent host is the daemon ``MAEL_AGENT_ROOT`` names, so a worktree's
    orchestrator talks to that worktree's daemon.
    """
    click.echo(f"Serving on http://{host}:{port}", err=True)
    try:
        run_server(host, port, log_level)
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
