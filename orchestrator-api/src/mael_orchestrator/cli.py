"""``mael-orchestrator`` — run the orchestrator server.

The thin CLI over :mod:`mael_orchestrator.server`. It wires the real
sources — the task notebook, ``list-all`` and the agent host's socket — into
an :class:`~mael_orchestrator.server.Orchestrator` and serves it. See
``docs/dev/orchestrator-server.md``.
"""

import asyncio
import logging
import signal
import ssl
import sys
from concurrent.futures import Executor, ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path

import click

from mael_agent.agent_transport import RootUnset, SocketAsyncDaemonClient, daemon_paths
from mael_domain import github
from mael_domain.agent_store import SqliteAgentStore, SqliteMilestoneStore
from mael_domain.cmux.mael_layout import MaelCmux
from mael_domain.context import load_global_config
from mael_domain.desk_store import SqliteDeskStore
from mael_domain.env import refresh_env
from mael_domain.env_store import JsonEnvStore
from mael_domain.github_model import GitHubError
from mael_domain.landing import Landings, project_config, tracked_tasks
from mael_domain.landing_github import GhLandingSignals
from mael_domain.landing_store import SqlitePullRequestStore, SqliteTaskStepStore
from mael_domain.notebook_root import NotebookRootUnset
from mael_domain.state_db.db import StateDb
from mael_domain.state_db.migrate import open_state_db
from mael_domain.state_db.paths import get_notebook_path, get_state_db_path
from mael_domain.state_db.types import StateDbError
from mael_domain.task_attachments import SqliteTaskAttachmentTable
from mael_domain.task_export import SqliteExportQueue, TaskExporter
from mael_domain.task_launch import LaunchBlocked
from mael_domain.task_store import GitFileStore
from mael_domain.task_table import SqliteTaskTable
from mael_domain.worktree import (
    WorktreeSetup,
    find_all_projects,
    setup_worktree_for_branch,
)
from mael_domain.worktree_close import (
    add_reopen_task,
    close_worktree_fully,
    remove_worktree_fully,
)
from mael_domain.worktree_model import WorktreeError, get_worktree_folder_name
from mael_domain.worktree_ops import run_env, run_sync
from mael_domain.worktree_trash import trash_worktree_fully

from .codex_bridge import CodexBridge
from .codex_daemon import CodexDaemonClient
from .daemon_bridge import DaemonRouter
from .routes import build_app, serve_app
from .server import Orchestrator
from .sources import (
    CloseBlocked,
    ListAllWorktreeSource,
    NotebookTaskSource,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

log = logging.getLogger(__name__)

#: How many worktree operations may run at once. Sized so a fleet of worktrees
#: overlaps rather than queues; the correctness rule is the steps' own scopes,
#: held with a cross-process lock — see :mod:`mael_domain.worktree_steps`.
WORKTREE_WORKERS = 4


def _refresh_env(
    project: str, nato: str, project_path: Path, worktree_path: Path
) -> None:
    """Rebuild a reopened worktree's ``.env``; any failure only warns."""
    try:
        refresh = refresh_env(
            JsonEnvStore(), project, nato, project_path, worktree_path
        )
    # Broad on purpose: a bad `.maelstrom.yaml` or an unwritable `.env` must
    # not refuse a launch.
    except Exception as exc:  # noqa: BLE001
        click.echo(f"Warning: .env not refreshed: {exc}", err=True)
        return
    for key in refresh.copy_back.added:
        click.echo(f"Copied {key} back to {project_path / '.env'}.", err=True)
    for conflict in refresh.copy_back.conflicts:
        click.echo(
            f"Warning: {conflict.key} differs from {project_path / '.env'}; "
            "the worktree value was overwritten.",
            err=True,
        )
    if refresh.changed:
        click.echo(f"Regenerated .env for {project}/{nato}.", err=True)


def build_orchestrator(
    *,
    executor: Executor | None = None,
    worktree_executor: Executor | None = None,
    db: StateDb | None = None,
) -> Orchestrator:
    """An orchestrator over the real notebook, ``list-all`` and agent host.

    The agent host is the daemon this environment names in ``MAEL_AGENT_ROOT``,
    so the orchestrator a worktree runs talks to that worktree's daemon.
    ``executor`` runs what still blocks; the task table does not, because it
    is the state database and binds to the loop's own thread.
    ``worktree_executor`` runs the worktree operations, which touch git and the
    process table and must not queue behind a task read.

    ``db`` is the state database, which now holds the tasks as well as the
    desk. It is never migrated here: an ordinary open refuses a database behind
    this build and names ``mael admin migrate``, which is the point of the
    refusal.
    """
    projects_dir = load_global_config().projects_dir
    # Resolved once: the tasks and the desk share one database, so opening a
    # second here would give the two halves of the world separate connections
    # and separate revision counters.
    state_db = db if db is not None else open_state_db()
    table = SqliteTaskTable(state_db)

    def open_worktree(project: str, branch: str, base: str) -> WorktreeSetup:
        # The launcher owns install; ``base`` seeds the branch's stored base
        # the first time, as ``mael task run`` does.
        #
        # A worktree that cannot be opened is a refused launch, not a crashed
        # server: LaunchBlocked is the wire's word for "this task could not
        # start, and here is why", so the domain errors are converted to it.
        try:
            setup = setup_worktree_for_branch(
                projects_dir / project,
                project,
                branch,
                run_install=False,
                base=base or None,
                announce=lambda line: click.echo(line, err=True),
            )
        except (ValueError, WorktreeError) as exc:
            raise LaunchBlocked(str(exc)) from exc
        if setup.rebuilds_env:
            _refresh_env(project, setup.name, projects_dir / project, setup.path)
        return setup

    async def close_worktree(project: str, nato: str, path: str) -> None:
        # Not forced: unmerged work is refused, and the model's own message is
        # what the button shows. Forcing is its own command, behind a confirm
        # the UI owns — see ``force_close_worktree`` below.
        outcome = await close_worktree_fully(
            project,
            nato,
            Path(path),
            projects_dir / project,
            force=False,
            executor=worktree_executor,
        )
        if not outcome.close.success:
            raise CloseBlocked(outcome.close.message)

    async def force_close_worktree(project: str, nato: str, path: str) -> None:
        # A decision, not a retry — see docs/dev/orchestrator-server.md,
        # "Closing a worktree".
        outcome = await close_worktree_fully(
            project,
            nato,
            Path(path),
            projects_dir / project,
            force=True,
            executor=worktree_executor,
        )
        if not outcome.close.success:
            raise CloseBlocked(outcome.close.message)
        try:
            await add_reopen_task(table, project, outcome.close)
        except Exception:  # noqa: BLE001 — logged with its traceback
            # The worktree is closed. A failed task write must not report
            # that close as failed, which is what raising here would do.
            log.exception("could not write the reopen task for %s/%s", project, nato)

    async def trash_worktree(project: str, nato: str, path: str) -> None:
        outcome = await trash_worktree_fully(
            project,
            nato,
            Path(path),
            projects_dir / project,
            executor=worktree_executor,
        )
        if not outcome.close.success:
            raise CloseBlocked(outcome.close.message)

    async def remove_worktree(project: str, nato: str, path: str) -> None:
        # Deletes the checkout rather than parking it. The teardown is the
        # close's, so the daemon's agents stop before any pid is signalled.
        # Not forced: a worktree holding uncommitted work is refused, and the
        # files are named. The UI has no prompt of its own to fall back on.
        outcome = await remove_worktree_fully(
            project,
            nato,
            Path(path),
            projects_dir / project,
            get_worktree_folder_name(project, nato),
            executor=worktree_executor,
        )
        if not outcome.close.success:
            raise CloseBlocked(outcome.close.message)

    async def sync_worktree(project: str, nato: str, path: str, mode: str) -> None:
        # A sequence like the teardowns, so it takes the worktree scope: a
        # rebase must not reach a checkout a close is already detaching.
        ran = await run_sync(
            project,
            nato,
            Path(path),
            projects_dir / project,
            mode,
            executor=worktree_executor,
        )
        if not ran.ok:
            raise CloseBlocked(ran.blocked or "The sync did not finish")

    def merge_worktree_pr(path: str, number: int, head_oid: str) -> None:
        try:
            github.merge_pr(
                number, cwd=Path(path), head_oid=head_oid, token=github.merge_token()
            )
        except GitHubError as exc:
            raise CloseBlocked(str(exc)) from exc

    async def env_worktree(
        project: str, nato: str, path: str, action: str, service: str | None
    ) -> None:
        ran = await run_env(
            project,
            nato,
            Path(path),
            projects_dir / project,
            action,
            service=service,
            executor=worktree_executor,
        )
        if not ran.ok:
            raise CloseBlocked(ran.blocked or "The environment did not change")

    def ensure_worktree_terminal(project: str, nato: str, path: str) -> str:
        cmux = MaelCmux.current()
        url = cmux.worktree(project, nato, path).ensure_terminal() if cmux else None
        if url is None:
            raise CloseBlocked("cmux could not make the terminal")
        return url

    def terminal_urls(worktrees: list[tuple[str, str]]) -> dict[tuple[str, str], str]:
        cmux = MaelCmux.current()
        return cmux.terminal_urls(worktrees) if cmux else {}

    agent_store = SqliteAgentStore(state_db)

    def list_projects() -> list[str]:
        return [path.name for path in find_all_projects(projects_dir)]

    tasks = NotebookTaskSource(
        table,
        list_projects,
        open_worktree=open_worktree,
        agents=agent_store,
    )
    worktrees = ListAllWorktreeSource(
        projects_dir,
        close=close_worktree,
        force_close=force_close_worktree,
        trash=trash_worktree,
        remove=remove_worktree,
        sync=sync_worktree,
        merge_pr=merge_worktree_pr,
        env=env_worktree,
        ensure_terminal=ensure_worktree_terminal,
        terminal_urls=terminal_urls,
    )
    daemon = DaemonRouter(
        SocketAsyncDaemonClient(str(daemon_paths().socket)),
        CodexDaemonClient(CodexBridge()),
        agent_store,
    )
    return Orchestrator(
        tasks,
        worktrees,
        daemon,
        desk=SqliteDeskStore(state_db),
        milestones=SqliteMilestoneStore(state_db),
        task_attachments=SqliteTaskAttachmentTable(state_db),
        landings=Landings(
            prs=SqlitePullRequestStore(state_db),
            steps=SqliteTaskStepStore(state_db),
            signals=GhLandingSignals(projects_dir),
            tracked=lambda: tracked_tasks(table, list_projects()),
            config_for=lambda project: project_config(projects_dir, project),
        ),
        # The one drainer. A CLI write queues its export and exits, so the
        # server is what writes the tree — which is also what leaves one writer
        # against the notebook's git repo rather than a process per command.
        exporter=TaskExporter(
            SqliteExportQueue(state_db),
            table,
            GitFileStore(root=get_notebook_path()),
            # The store takes a cross-process lock and shells out to git, so
            # its calls run off the loop the server answers sockets on.
            executor=executor,
        ),
        executor=executor,
        worktree_executor=worktree_executor,
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
    logging.getLogger("mael_orchestrator").error(
        "unhandled error in the event loop: %s",
        context.get("message", "(no message)"),
        exc_info=context.get("exception"),
    )


def run_server(
    host: str,
    port: int,
    log_level: str = DEFAULT_LOG_LEVEL,
    *,
    ssl_context: ssl.SSLContext | None = None,
) -> None:
    """Build the orchestrator and serve it until interrupted.

    An ``ssl_context`` serves HTTPS, for a page that is a secure context.

    The worker pool lives for the serve call, so an interrupt does not wait on
    a read in flight past the point the server has stopped.
    """
    setup_logging(log_level)

    async def serve() -> None:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(_log_unhandled)
        # An ordinary open refuses a database this build cannot read, rather
        # than upgrading it under whatever else is using ~/.maelstrom. The
        # check runs on the loop, because that is the thread the connection
        # binds to and every later call has to come from the same one.
        await db.check()
        serving = asyncio.ensure_future(
            serve_app(build_app(orchestrator), host, port, ssl_context=ssl_context)
        )
        # A supervisor stops the server with SIGTERM. Without a handler the
        # default terminates the process outright, so the app never cleans up
        # and the orchestrator never stops its pollers or flushes the desk.
        for signum in (signal.SIGTERM, signal.SIGINT):
            with suppress(NotImplementedError):
                loop.add_signal_handler(signum, serving.cancel)
        with suppress(asyncio.CancelledError):
            await serving

    # The directory may not exist on a machine that has never run a mael
    # command. sqlite3 raises OperationalError rather than creating it, and
    # that is not a StateDbError, so `cmd_serve` would show a traceback.
    path = get_state_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = open_state_db(path)
    # One worker, not a pool: the SQLite index behind the notebook is bound to
    # the thread that opened it, so every blocking read must run on the same one.
    # The state database is bound the same way, but to the loop's own thread:
    # the desk never goes through the executor.
    #
    # The worktree pool is separate and wider. Worktree work touches git, ports
    # and the process table, never the notebook, so it has no reason to queue
    # behind a task read — and a fetch would stall one for seconds. Its size is
    # for overlap, not for correctness: what must not run at once is named by
    # the steps' own scopes and held with a cross-process lock, so reducing this
    # to one would only make a fleet of worktrees as slow as a queue.
    # See mael_domain.worktree_steps.
    try:
        with (
            ThreadPoolExecutor(max_workers=1) as executor,
            ThreadPoolExecutor(
                max_workers=WORKTREE_WORKERS, thread_name_prefix="worktree"
            ) as worktree_executor,
        ):
            orchestrator = build_orchestrator(
                executor=executor, worktree_executor=worktree_executor, db=db
            )
            asyncio.run(serve())
    finally:
        db.close()


@click.group("mael-orchestrator")
def cli() -> None:
    """Serve the world to the orchestrator UI."""


@cli.command("serve")
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
@click.option(
    "--tls-cert",
    envvar="DEV_TLS_CERT",
    type=click.Path(exists=True, dir_okay=False),
    help="Certificate file. Serves HTTPS with --tls-key. [env: DEV_TLS_CERT]",
)
@click.option(
    "--tls-key",
    envvar="DEV_TLS_KEY",
    type=click.Path(exists=True, dir_okay=False),
    help="Private key file for --tls-cert. [env: DEV_TLS_KEY]",
)
def cmd_serve(
    host: str, port: int, log_level: str, tls_cert: str | None, tls_key: str | None
) -> None:
    """Run the orchestrator server in the foreground.

    The agent host is the daemon ``MAEL_AGENT_ROOT`` names, so a worktree's
    orchestrator talks to that worktree's daemon. The TLS options default to
    the dev certificate a worktree ``.env`` names under ``dev_https:``.
    """
    ssl_context = None
    if tls_cert or tls_key:
        if not (tls_cert and tls_key):
            raise click.UsageError("--tls-cert and --tls-key go together.")
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain(tls_cert, tls_key)
    scheme = "https" if ssl_context else "http"
    click.echo(f"Serving on {scheme}://{host}:{port}", err=True)
    try:
        run_server(host, port, log_level, ssl_context=ssl_context)
    except (StateDbError, NotebookRootUnset, RootUnset) as exc:
        # The refusal already names the fix; repeating it as a traceback would
        # bury it.
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        # Only a Ctrl-C before the loop starts reaches here. Once it is
        # running, the SIGINT handler cancels the serve task instead.
        pass
    except OSError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


def main() -> None:
    """The ``mael-orchestrator`` console script."""
    cli()
