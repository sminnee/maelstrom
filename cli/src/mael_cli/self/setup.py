"""``mael self setup``: bring the host to the state the release wants.

``bin/install.sh`` runs it from the new release after the switch, with the
components that changed. A user runs it to repair a host. Every step is safe
to repeat, so a setup applies them all, and restarts only the host services
whose components changed. The step order matters: see
``docs/dev/host-services.md``.
"""

import asyncio
import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from mael_domain.context import (
    GLOBAL_CONFIG_FILENAME,
    harden_global_config,
    load_config_at,
    read_config_key,
    remove_config_key,
    set_config_key,
)
from mael_domain.dev_cert import cert_paths
from mael_domain.env import load_env_state, stop_env
from mael_domain.env_store import JsonEnvStore
from mael_domain.state_db.migrate import migrate_state_db
from mael_domain.worktree_model import MAIN_WORKTREE_FOLDER

from .claude_integration import install_claude_integration
from .host_service import (
    AGENT_DAEMON,
    ORCHESTRATOR,
    RESTARTED_BY,
    SCHEDULER,
    HostService,
    Run,
    Supervisor,
)
from .production import (
    SERVICE_PATH_KEY,
    Production,
    production_env,
    store_service_path,
    write_shim,
)
from .update import JIG_PACKAGE, MAELSTROM_PROJECT, link_jig

#: The orchestrator's port when setup picks the URL. It is the port `_main`'s
#: env-managed orchestrator had (`main_port_base: 277`, index 2), so bookmarks
#: and clients that dial it keep working.
ORCHESTRATOR_DEFAULT_PORT = 2772

ORCHESTRATOR_URL_KEY = "orchestrator_url"

#: The files of the production notebook that relocation moves. The WAL files go
#: before the database, so a database never sits without its uncheckpointed log.
_OLD_NOTEBOOK_FILES = ("state.db-wal", "state.db-shm", "state.db", "tasks", "desk.json")

#: The components whose host services have not restarted yet, written by
#: ``install.sh``. A setup that stops early leaves it for the next one.
PENDING_CHANGED = "pending-changed"

#: The marker that once opted a host into the scheduler. It is always on now.
_OLD_SCHEDULE_MARKER = "schedule.enabled"

Echo = Callable[[str], None]


class SetupError(RuntimeError):
    """Setup could not bring the host to the state the release wants."""


@dataclass(frozen=True)
class OrchestratorBind:
    """How the orchestrator host service listens, read from its URL."""

    host: str
    port: int
    tls: tuple[Path, Path] | None = None

    @classmethod
    def from_url(cls, url: str) -> "OrchestratorBind":
        parts = urlsplit(url)
        host = parts.hostname or "localhost"
        try:
            port = parts.port or ORCHESTRATOR_DEFAULT_PORT
        except ValueError as exc:
            raise SetupError(f"{ORCHESTRATOR_URL_KEY} {url!r} has a bad port") from exc
        local = host in ("localhost", "127.0.0.1")
        tls = cert_paths(host) if parts.scheme == "https" else None
        return cls(host="127.0.0.1" if local else "0.0.0.0", port=port, tls=tls)


def host_services(
    prod: Production, bind: OrchestratorBind, inherited_path: str
) -> list[HostService]:
    """The orchestrator and the agent daemon, as the supervisor runs them."""
    env = production_env(prod, inherited_path)
    orchestrator_env = dict(env)
    if bind.tls is not None:
        cert, key = bind.tls
        orchestrator_env |= {"DEV_TLS_CERT": str(cert), "DEV_TLS_KEY": str(key)}
    return [
        HostService(
            name=ORCHESTRATOR,
            argv=(
                str(prod.bin / "mael-orchestrator"),
                "serve",
                "--host",
                bind.host,
                "--port",
                str(bind.port),
            ),
            env=orchestrator_env,
            cwd=prod.state,
            log=prod.service_logs / f"{ORCHESTRATOR}.log",
        ),
        HostService(
            name=AGENT_DAEMON,
            argv=(str(prod.bin / "mael-agent-daemon"), "serve"),
            env=env,
            cwd=prod.state,
            log=prod.service_logs / f"{AGENT_DAEMON}.log",
        ),
    ]


def scheduler(prod: Production, inherited_path: str) -> HostService:
    """The hourly run of due template tasks."""
    return HostService(
        name=SCHEDULER,
        argv=(str(prod.shim), "task", "add-scheduled", "--all-projects", "--run"),
        env=production_env(prod, inherited_path),
        hourly=True,
        cwd=prod.state,
        log=prod.maelstrom / "schedule.log",
    )


def configured_services(prod: Production, inherited_path: str) -> list[HostService]:
    """The host services, with the bind the notebook's config names."""
    bind = OrchestratorBind.from_url(orchestrator_url(prod) or "http://localhost")
    return host_services(prod, bind, inherited_path)


def all_units(prod: Production, inherited_path: str) -> list[HostService]:
    """Every unit setup installs: the host services and the scheduler."""
    return [*configured_services(prod, inherited_path), scheduler(prod, inherited_path)]


def orchestrator_url(prod: Production) -> str | None:
    """The URL the production notebook's clients dial."""
    return load_config_at(prod.state).orchestrator_url


def restarts_for(changed: Sequence[str]) -> list[str]:
    """The host services the changed components restart, in a fixed order."""
    names = {name for c in changed for name in RESTARTED_BY.get(c, ())}
    return [n for n in (ORCHESTRATOR, AGENT_DAEMON) if n in names]


def tailnet_name(run: Run) -> str | None:
    """This machine's tailnet DNS name, or ``None`` without a working tailscale."""
    result = run(["tailscale", "status", "--json"])
    if result.returncode != 0:
        return None
    try:
        name = json.loads(result.stdout)["Self"]["DNSName"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(name, str):
        return None
    return name.rstrip(".") or None


def default_orchestrator_url(prod: Production, run: Run) -> str:
    """The URL setup writes when the user set none.

    The host is ``dev_host:``, else the tailnet name, else ``localhost``. The
    scheme is ``https`` only when the dev certificate names that host.
    """
    config = load_config_at(prod.state)
    host = config.dev_host or tailnet_name(run) or "localhost"
    scheme = "https" if config.tls_host == host else "http"
    return f"{scheme}://{host}:{ORCHESTRATOR_DEFAULT_PORT}"


def _stop_main_services(echo: Echo) -> None:
    """Stop `_main`'s env-managed orchestrator and daemon, if they run.

    The host services take their port and their daemon root.
    """
    store = JsonEnvStore()
    state = load_env_state(store, MAELSTROM_PROJECT, MAIN_WORKTREE_FOLDER)
    running = {s.name for s in state.services} if state else set()
    names = [n for n in (ORCHESTRATOR, AGENT_DAEMON) if n in running]
    if not names:
        return
    echo(
        f"Stopping {', '.join(names)} in {MAELSTROM_PROJECT}/{MAIN_WORKTREE_FOLDER}..."
    )
    for line in stop_env(
        store, MAELSTROM_PROJECT, MAIN_WORKTREE_FOLDER, services=names
    ):
        echo(f"  {line}")


@dataclass
class Setup:
    """One setup run, with every outside effect injected."""

    prod: Production
    supervisor: Supervisor
    run: Run
    echo: Echo
    changed: Sequence[str] = ()
    source: Path | None = None
    #: The PATH the units carry, from the caller.
    inherited_path: str = field(default_factory=lambda: os.environ.get("PATH", ""))
    stop_main_services: Callable[[Echo], None] = _stop_main_services
    integrate: Callable[[], list[str]] = install_claude_integration
    harden: Callable[[], list[str]] = harden_global_config
    link_jig: Callable[[Path, Echo], None] = link_jig
    #: The host services that did not load or restart.
    failed: list[str] = field(default_factory=list)

    def __call__(self) -> None:
        self.relocate()
        self.migrate()
        self.echo("Updating Claude Code integration...")
        for msg in [*self.integrate(), *self.harden()]:
            self.echo(f"  {msg}")
        self.echo(f"  {write_shim(self.prod)}")
        if self.source is not None:
            # A developer override: everyone else gets the npm release.
            self.link_jig(self.source / JIG_PACKAGE, self.echo)
        self.services()
        self.schedule()
        if self.failed:
            raise SetupError(
                f"{', '.join(self.failed)} did not start. Fix the error above, "
                "then run `mael self setup` again."
            )
        self.echo("Setup complete.")

    def relocate(self) -> None:
        """Move the production state of an older install into ``state/``, once.

        It runs only when ``~/.maelstrom/state.db`` exists and
        ``state/state.db`` does not. Every move is a rename on one filesystem.
        The daemon's spawn records store no root path, so they move as they are.
        """
        old, new = self.prod.maelstrom, self.prod.state
        if not (old / "state.db").exists():
            return
        if (new / "state.db").exists():
            self.echo(
                f"  Warning: {old / 'state.db'} is an older notebook, and "
                f"{new / 'state.db'} is the one in use. Move or delete the old one."
            )
            return
        self.echo(f"Moving the notebook and the daemon root into {new}...")
        self.supervisor.remove(configured_services(self.prod, self.inherited_path))
        self.stop_main_services(self.echo)
        new.mkdir(parents=True, exist_ok=True)
        moves = [(old / name, new / name) for name in _OLD_NOTEBOOK_FILES]
        moves.append((old / "daemons" / MAIN_WORKTREE_FOLDER, self.prod.daemon_root))
        for src, dst in moves:
            if not src.exists():
                continue
            if dst.exists():
                self.echo(f"  Warning: {dst} exists, so {src} stays where it is.")
                continue
            src.rename(dst)
            self.echo(f"  {src} → {dst}")

    def migrate(self) -> None:
        """Migrate ``state.db``, and give the notebook its orchestrator URL and unit PATH."""
        self.echo("Migrating the state database...")
        asyncio.run(migrate_state_db(self.prod.state / "state.db"))
        local = self.prod.state / GLOBAL_CONFIG_FILENAME
        global_ = self.prod.maelstrom / GLOBAL_CONFIG_FILENAME
        # The key names the notebook's server, so it lives with the notebook.
        moved = read_config_key(global_, ORCHESTRATOR_URL_KEY)
        if (
            isinstance(moved, str)
            and read_config_key(local, ORCHESTRATOR_URL_KEY) is None
        ):
            set_config_key(local, ORCHESTRATOR_URL_KEY, moved)
            remove_config_key(global_, ORCHESTRATOR_URL_KEY)
            self.echo(f"  Moved {ORCHESTRATOR_URL_KEY} to {local}")
        if orchestrator_url(self.prod) is None:
            url = default_orchestrator_url(self.prod, self.run)
            set_config_key(local, ORCHESTRATOR_URL_KEY, url)
            self.echo(f"  {ORCHESTRATOR_URL_KEY}: {url}")
        stored = store_service_path(self.prod, self.inherited_path)
        if stored is not None:
            self.echo(f"  {SERVICE_PATH_KEY}: {stored}")

    def services(self) -> None:
        """Write the host service units, load them, restart the changed ones.

        The components ``install.sh`` left pending join ``changed``. The record
        goes only once every restart succeeded.
        """
        pending = self.prod.install_root / PENDING_CHANGED
        changed = {*self.changed}
        if pending.is_file():
            changed |= {c for c in pending.read_text().strip().split(",") if c}
        self.stop_main_services(self.echo)
        specs = configured_services(self.prod, self.inherited_path)
        orchestrator = specs[0].env
        for var in ("DEV_TLS_CERT", "DEV_TLS_KEY"):
            if var in orchestrator and not Path(orchestrator[var]).is_file():
                self.echo(
                    f"  Warning: {ORCHESTRATOR_URL_KEY} is https, and "
                    f"{orchestrator[var]} is missing. Run `mael env start` once to "
                    "issue the dev certificate."
                )
        applied = self.supervisor.apply(specs)
        wanted = restarts_for(sorted(changed))
        restart = [
            s for s in specs if s.name in wanted and s.name not in applied.started
        ]
        restarted = self.supervisor.restart(restart) if restart else None
        for result in (applied, restarted):
            if result is None:
                continue
            for msg in result.messages:
                self.echo(f"  {msg}")
            self.failed += result.failed
        if not self.failed:
            pending.unlink(missing_ok=True)

    def schedule(self) -> None:
        """Install the hourly scheduler. It is always on."""
        (self.prod.maelstrom / _OLD_SCHEDULE_MARKER).unlink(missing_ok=True)
        applied = self.supervisor.apply([scheduler(self.prod, self.inherited_path)])
        for msg in applied.messages:
            self.echo(f"  {msg}")
        self.failed += applied.failed
