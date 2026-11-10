"""``mael-orchestrator`` — run the orchestrator server.

The thin CLI over :mod:`mael_orchestrator.server`. It wires the real
sources — the task notebook, ``list-all`` and the agent host's socket — into
an :class:`~mael_orchestrator.server.Orchestrator` and serves it. See
``docs/dev/orchestrator-server.md``.
"""

import asyncio
import functools
import getpass
import ipaddress
import logging
import os
import signal
import socket
import ssl
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Executor, ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path

import click

from mael_agent.agent_transport import RootUnset, SocketAsyncDaemonClient, daemon_paths
from mael_agent.host_ids import IdSpace
from mael_domain import github
from mael_domain.agent_store import SqliteAgentStore, SqliteMilestoneStore
from mael_domain.cmux.mael_layout import MaelCmux
from mael_domain.comm_store import SqliteCommStore, SqliteCommUpdateStore
from mael_domain.config import MaelstromConfig
from mael_domain.context import load_global_config
from mael_domain.desk_store import SqliteDeskStore
from mael_domain.env import refresh_env
from mael_domain.env_store import JsonEnvStore
from mael_domain.github_model import GitHubError
from mael_domain.landing import Landings, project_config, tracked_tasks
from mael_domain.landing_github import GhLandingSignals
from mael_domain.landing_store import SqlitePullRequestStore, SqliteTaskStepStore
from mael_domain.language_servers import LANGUAGE_SERVERS
from mael_domain.notebook_root import NotebookRootUnset
from mael_domain.operation_store import SqliteOperationStore
from mael_domain.pr_ledger_store import SqlitePrLedgerStore
from mael_domain.release_bundle.layout import installed_web_root
from mael_domain.ssh_target import SshTarget
from mael_domain.state_db.db import StateDb
from mael_domain.state_db.migrate import open_state_db
from mael_domain.state_db.paths import get_state_db_path
from mael_domain.state_db.types import StateDbError
from mael_domain.system_info import SystemPaths
from mael_domain.task_attachments import SqliteTaskAttachmentTable
from mael_domain.task_launch import LaunchBlocked
from mael_domain.task_table import SqliteTaskTable
from mael_domain.worktree import (
    WorktreeSetup,
    add_project,
    find_all_projects,
    setup_worktree_for_branch,
)
from mael_domain.worktree_close import (
    close_worktree_fully,
    remove_worktree_fully,
    reopen_task_fields,
)
from mael_domain.worktree_model import WorktreeError, get_worktree_folder_name
from mael_domain.worktree_ops import run_env, run_sync
from mael_domain.worktree_steps import StepHook
from mael_domain.worktree_trash import trash_worktree_fully

from . import machine_metrics
from .codex_bridge import CodexBridge
from .codex_daemon import CodexDaemonClient
from .daemon_bridge import DaemonRouter
from .gate_source import watch_gates
from .language_servers import LanguageServers
from .remote_hosts import RemoteHost
from .routes import Bind, build_app, serve_app
from .server import WORKTREE_POLL_SECS, Orchestrator
from .sources import (
    CloseBlocked,
    ListAllWorktreeSource,
    NotebookTaskSource,
    TaskHome,
    WorktreeOpener,
)

DEFAULT_HOST = "127.0.0.1"
# Bind addresses that take every interface, so no other listener fits beside.
WILDCARD_HOSTS = ("0.0.0.0", "::", "")
DEFAULT_PORT = 8765

log = logging.getLogger(__name__)

#: How many worktree operations may run at once. Sized so a fleet of worktrees
#: overlaps rather than queues; the correctness rule is the steps' own scopes,
#: held with a cross-process lock — see :mod:`mael_domain.worktree_steps`.
WORKTREE_WORKERS = 4


def _refresh_env(project: str, project_path: Path, setup: WorktreeSetup) -> list[str]:
    """Rebuild an opened worktree's ``.env``, and say what it did.

    A failure only warns: a bad ``.maelstrom.yaml`` or an unwritable ``.env``
    must not refuse a launch. The lines are the launch's ``write_env`` step log.
    """
    try:
        refresh = refresh_env(
            JsonEnvStore(), project, setup.name, project_path, setup.path
        )
    except Exception as exc:  # noqa: BLE001 — broad on purpose, see above
        return [f"Warning: .env not refreshed: {exc}"]
    lines = [
        f"Copied {key} back to {project_path / '.env'}."
        for key in refresh.copy_back.added
    ]
    lines += [
        f"Warning: {conflict.key} differs from {project_path / '.env'}; "
        "the worktree value was overwritten."
        for conflict in refresh.copy_back.conflicts
    ]
    if refresh.changed:
        lines.append(f"Regenerated .env for {project}/{setup.name}.")
    return lines or [".env unchanged."]


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
    desk. It is never migrated here: an ordinary open refuses a database
    behind this build.
    """
    config = load_global_config()
    projects_dir = config.projects_dir
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
                token=github.orchestrator_token(),
                announce=lambda line: click.echo(line, err=True),
            )
        except (ValueError, WorktreeError) as exc:
            raise LaunchBlocked(str(exc)) from exc
        if setup.sync is not None and setup.sync.push_failed:
            # The rebase landed, so the launch goes ahead on the local branch.
            log.warning("open of %s/%s: %s", project, branch, setup.sync.push_message)
        return setup

    def write_env(project: str, setup: WorktreeSetup) -> list[str]:
        return _refresh_env(project, projects_dir / project, setup)

    async def close_worktree(
        project: str, nato: str, path: str, *, hook: StepHook
    ) -> None:
        # Not forced: unmerged work is refused, and the model's own message is
        # what the button shows. Forcing is its own command, behind a confirm
        # the UI owns — see ``force_close_worktree`` below.
        outcome = await close_worktree_fully(
            project,
            nato,
            Path(path),
            projects_dir / project,
            force=False,
            token=github.orchestrator_token(),
            executor=worktree_executor,
            hook=hook,
        )
        if not outcome.close.success:
            raise CloseBlocked(outcome.close.message)

    async def force_close_worktree(
        project: str, nato: str, path: str, *, hook: StepHook, home: TaskHome
    ) -> None:
        # A decision, not a retry — see docs/dev/orchestrator-server.md,
        # "Closing a worktree".
        outcome = await close_worktree_fully(
            project,
            nato,
            Path(path),
            projects_dir / project,
            force=True,
            token=github.orchestrator_token(),
            executor=worktree_executor,
            hook=hook,
        )
        if not outcome.close.success:
            raise CloseBlocked(outcome.close.message)
        fields = reopen_task_fields(outcome.close)
        if fields is None:
            return
        try:
            await home.create(project, fields)
        except Exception:  # noqa: BLE001 — logged with its traceback
            # The worktree is closed. A failed task write must not report
            # that close as failed, which is what raising here would do.
            log.exception("could not write the reopen task for %s/%s", project, nato)

    async def trash_worktree(
        project: str, nato: str, path: str, *, hook: StepHook
    ) -> None:
        outcome = await trash_worktree_fully(
            project,
            nato,
            Path(path),
            projects_dir / project,
            token=github.orchestrator_token(),
            executor=worktree_executor,
            hook=hook,
        )
        if not outcome.close.success:
            raise CloseBlocked(outcome.close.message)

    async def remove_worktree(
        project: str, nato: str, path: str, *, hook: StepHook
    ) -> None:
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
            hook=hook,
        )
        if not outcome.close.success:
            raise CloseBlocked(outcome.close.message)

    async def sync_worktree(
        project: str, nato: str, path: str, mode: str, *, hook: StepHook
    ) -> None:
        # A sequence like the teardowns, so it takes the worktree scope: a
        # rebase must not reach a checkout a close is already detaching.
        ran = await run_sync(
            project,
            nato,
            Path(path),
            projects_dir / project,
            mode,
            token=github.orchestrator_token(),
            executor=worktree_executor,
            hook=hook,
        )
        if not ran.ok:
            raise CloseBlocked(ran.blocked or "The sync did not finish")

    def merge_worktree_pr(path: str, number: int, head_oid: str) -> None:
        try:
            github.merge_pr(
                number,
                cwd=Path(path),
                head_oid=head_oid,
                token=github.orchestrator_token(),
            )
        except GitHubError as exc:
            log.warning("merge of PR %d in %s refused: %s", number, path, exc)
            raise CloseBlocked(str(exc)) from exc

    def auto_merge_worktree_pr(path: str, number: int, head_oid: str, on: bool) -> None:
        try:
            github.set_auto_merge(
                number,
                cwd=Path(path),
                head_oid=head_oid,
                on=on,
                token=github.orchestrator_token(),
            )
        except GitHubError as exc:
            log.warning("auto-merge of PR %d in %s refused: %s", number, path, exc)
            raise CloseBlocked(str(exc)) from exc

    async def env_worktree(
        project: str,
        nato: str,
        path: str,
        action: str,
        service: str | None,
        *,
        hook: StepHook,
    ) -> None:
        ran = await run_env(
            project,
            nato,
            Path(path),
            projects_dir / project,
            action,
            service=service,
            executor=worktree_executor,
            hook=hook,
        )
        if not ran.ok:
            raise CloseBlocked(ran.blocked or "The environment did not change")

    def ensure_worktree_terminal(project: str, nato: str, path: str) -> str:
        cmux = MaelCmux.current()
        url = cmux.worktree(project, nato, path).ensure_terminal() if cmux else None
        if url is None:
            raise CloseBlocked("cmux could not make the terminal")
        return url

    def make_ssh_terminal(name: str, target: SshTarget) -> str:
        cmux = MaelCmux.current()
        url = cmux.ssh_terminal(name, target) if cmux else None
        if url is None:
            raise CloseBlocked("cmux could not make the terminal")
        return url

    def terminal_urls(worktrees: list[tuple[str, str]]) -> dict[tuple[str, str], str]:
        cmux = MaelCmux.current()
        return cmux.terminal_urls(worktrees) if cmux else {}

    agent_store = SqliteAgentStore(state_db)

    def list_projects() -> list[str]:
        return [path.name for path in find_all_projects(projects_dir)]

    landings = Landings(
        prs=SqlitePullRequestStore(state_db),
        steps=SqliteTaskStepStore(state_db),
        signals=GhLandingSignals(projects_dir),
        tracked=lambda: tracked_tasks(table, list_projects()),
        config_for=_config_reader(projects_dir),
    )
    tasks = NotebookTaskSource(
        table,
        list_projects,
        agents=agent_store,
        landings=landings,
        # Read at start: a config change needs an orchestrator restart.
        schedule_zone=config.schedule_zone_label(),
    )
    opener = WorktreeOpener(
        open_worktree, write_env, worktree_executor=worktree_executor
    )
    worktrees = ListAllWorktreeSource(
        projects_dir,
        close=close_worktree,
        force_close=force_close_worktree,
        trash=trash_worktree,
        remove=remove_worktree,
        sync=sync_worktree,
        merge_pr=merge_worktree_pr,
        auto_merge=auto_merge_worktree_pr,
        env=env_worktree,
        ensure_terminal=ensure_worktree_terminal,
        terminal_urls=terminal_urls,
        add_project=functools.partial(clone_project, projects_dir),
        ssh_terminal=make_ssh_terminal,
        # Read at start: a config change needs an orchestrator restart.
        ssh_login=(getpass.getuser(), config.dev_host) if config.dev_host else None,
    )
    # The one place the id prefix is read: every reader below is given it.
    ids = IdSpace(config.host_name)
    daemon = DaemonRouter(
        SocketAsyncDaemonClient(str(daemon_paths().socket)),
        CodexDaemonClient(CodexBridge()),
        agent_store,
        ids=ids,
    )
    return Orchestrator(
        tasks,
        worktrees,
        daemon,
        desk=SqliteDeskStore(state_db),
        milestones=SqliteMilestoneStore(state_db),
        document_store=SqliteTaskAttachmentTable(state_db),
        landings=landings,
        comms=SqliteCommStore(state_db),
        comm_updates=SqliteCommUpdateStore(state_db),
        operations=SqliteOperationStore(state_db),
        pr_ledger=SqlitePrLedgerStore(state_db),
        executor=executor,
        worktree_executor=worktree_executor,
        gate_watch=watch_gates,
        read_machine=machine_metrics.read_machine,
        language_servers=LanguageServers(LANGUAGE_SERVERS),
        system=SystemPaths.for_home(
            Path.home(),
            projects_dir,
            sys.prefix,
            production=os.environ.get("MAEL_PRODUCTION") == "1",
        ),
        remote_hosts={
            name: RemoteHost(name, url) for name, url in config.hosts.items()
        },
        opener=opener,
        ids=ids,
        own_url=config.orchestrator_url or "",
        own_name=config.server_name(),
    )


#: What git says when an HTTPS clone needs a password and may not ask.
NO_CREDENTIALS = "terminal prompts disabled"
#: Appended to a refusal that holds ``NO_CREDENTIALS``.
NO_CREDENTIALS_HINT = (
    "git has no credentials for this host. For GitHub, run `gh auth setup-git` "
    "on the host, or `mael self check-host`, then add the project again."
)


def clone_project(projects_dir: Path, git_url: str) -> None:
    """``mael project add`` for the server, with no one at the terminal.

    A failed clone is refused with git's own words, which are in stderr: the
    exception says only the exit status.
    """
    try:
        add_project(git_url, projects_dir, batch=True)
    except subprocess.CalledProcessError as exc:
        words = (exc.stderr or "").strip() or str(exc)
        if NO_CREDENTIALS in words:
            words = f"{words}\n{NO_CREDENTIALS_HINT}"
        raise CloseBlocked(words) from exc
    except subprocess.TimeoutExpired as exc:
        raise CloseBlocked(f"The clone took more than {exc.timeout:.0f}s") from exc
    except ValueError as exc:
        # The folder exists, though the world holds no such project.
        raise CloseBlocked(str(exc)) from exc


def _config_reader(
    projects_dir: Path, ttl: float = WORKTREE_POLL_SECS
) -> Callable[[str], MaelstromConfig]:
    """``project_config``, read from disk at most once per ``ttl`` per project.

    A whole task read asks it once per done task, so an uncached read would
    parse a project's ``.maelstrom.yaml`` hundreds of times. An edit to the
    ``deploy:`` block shows within one worktree poll.
    """
    cache: dict[str, tuple[float, MaelstromConfig]] = {}

    def read(project: str) -> MaelstromConfig:
        now = time.monotonic()
        hit = cache.get(project)
        if hit is None or now - hit[0] >= ttl:
            hit = cache[project] = (now, project_config(projects_dir, project))
        return hit[1]

    return read


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
    binds: Sequence[Bind],
    port: int,
    log_level: str = DEFAULT_LOG_LEVEL,
    *,
    web_root: Path | None = None,
) -> None:
    """Build the orchestrator and serve it on each bind until interrupted.

    A bind with an SSL context serves HTTPS, for a page that is a secure
    context. A ``web_root`` serves the built UI beside the API.

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
            serve_app(build_app(orchestrator, web_root=web_root), binds, port)
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


def resolve_ipv4(name: str) -> list[str]:
    """The IPv4 addresses ``name`` resolves to. Raises ``OSError`` when none."""
    infos = socket.getaddrinfo(name, None, socket.AF_INET, socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def public_addresses(name: str, resolve: Callable[[str], list[str]]) -> list[str]:
    """The addresses a ``--public-host`` listener binds: ``name``'s, less loopback.

    Not ``0.0.0.0``: a wildcard takes loopback too, where the plain listener
    is, and it would serve on any network the machine joins.
    """
    found = (a for a in resolve(name) if not ipaddress.ip_address(a).is_loopback)
    return list(dict.fromkeys(found))


def _tls_given_as_option() -> bool:
    """Whether a TLS option came from the command line, not the environment."""
    ctx = click.get_current_context()
    return any(
        ctx.get_parameter_source(name) == click.core.ParameterSource.COMMANDLINE
        for name in ("tls_cert", "tls_key")
    )


@click.group("mael-orchestrator")
def cli() -> None:
    """Serve the world to the orchestrator UI."""


@cli.command("serve")
@click.option(
    "--host",
    default=DEFAULT_HOST,
    show_default=True,
    help="Bind address. It serves plain HTTP.",
)
@click.option(
    "--public-host",
    help="A host name to listen on as well, on each IPv4 address it resolves "
    "to. Loopback addresses are skipped. This listener alone serves TLS.",
)
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
@click.option(
    "--web-root",
    envvar="MAEL_WEB_ROOT",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="The built UI to serve beside the API. Default: the installed "
    "release's web component, if this runs from one. [env: MAEL_WEB_ROOT]",
)
def cmd_serve(
    host: str,
    public_host: str | None,
    port: int,
    log_level: str,
    tls_cert: str | None,
    tls_key: str | None,
    web_root: Path | None,
) -> None:
    """Run the orchestrator server in the foreground.

    The agent host is the daemon ``MAEL_AGENT_ROOT`` names, so a worktree's
    orchestrator talks to that worktree's daemon. The TLS options default to
    the dev certificate a worktree ``.env`` names under ``dev_https:``, and
    apply to the ``--public-host`` listener only.
    """
    if web_root is None:
        web_root = installed_web_root(sys.prefix)
    if public_host and host in WILDCARD_HOSTS:
        raise click.UsageError(
            f"--host {host} takes every address, so --public-host cannot bind."
        )
    if not public_host and not _tls_given_as_option():
        # A worktree .env names the certificate under dev_https:, and a serve
        # with no public listener has no use for it.
        tls_cert = tls_key = None
    ssl_context = None
    if tls_cert or tls_key:
        if not (tls_cert and tls_key):
            raise click.UsageError("--tls-cert and --tls-key go together.")
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain(tls_cert, tls_key)
    public: list[str] = []
    if public_host:
        try:
            public = public_addresses(public_host, resolve_ipv4)
        except OSError as exc:
            # A supervisor restarts the unit, so a server that starts before
            # the tailnet is up tries again.
            click.echo(f"Error: --public-host {public_host}: {exc}", err=True)
            sys.exit(1)
    if ssl_context and not public:
        raise click.UsageError(
            "--tls-cert and --tls-key need a --public-host that is not loopback."
        )
    binds: list[Bind] = [(host, None), *((a, ssl_context) for a in public)]
    click.echo(f"Serving on http://{host}:{port}", err=True)
    scheme = "https" if ssl_context else "http"
    for address in public:
        click.echo(f"Serving on {scheme}://{public_host}:{port} ({address})", err=True)
    try:
        run_server(binds, port, log_level, web_root=web_root)
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
