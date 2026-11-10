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
import re
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from mael_domain.context import (
    GLOBAL_CONFIG_FILENAME,
    PRODUCTION_ORCHESTRATOR_PORT,
    GlobalConfig,
    harden_global_config,
    load_config_file,
    read_config_key,
    remove_config_key,
    set_config_key,
)
from mael_domain.dev_cert import DevCertError, cert_paths, ensure_dev_cert
from mael_domain.env import load_env_state, stop_env
from mael_domain.env_store import JsonEnvStore
from mael_domain.shared_dir import get_shared_dir
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
from .plugins import Plugins, codex_home_from_env
from .production import (
    SERVICE_PATH_KEY,
    Production,
    agent_path,
    production_env,
    store_service_path,
    write_shim,
)
from .update import JIG_PACKAGE, MAELSTROM_PROJECT, link_jig

#: The key an older setup wrote. Production now derives the URL, so setup
#: deletes it.
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
#: Asks the user ``text``, offering ``default``, and returns the answer.
Prompt = Callable[[str, str], str]

PROJECTS_DIR_KEY = "projects_dir"
DEFAULT_PROJECTS_DIR = "~/Projects"

#: The global config's name before ``~/.maelstrom/`` existed. No longer read.
LEGACY_CONFIG_FILENAME = ".maelstrom.yaml"


def _yaml_scalar(value: str) -> str:
    """``value`` as one YAML scalar: plain when it is a simple path, else quoted.

    A bare ``~`` is quoted, because plain ``~`` is YAML null.
    """
    if value != "~" and re.fullmatch(r"[\w./~-]+", value):
        return value
    return json.dumps(value)


class SetupError(RuntimeError):
    """Setup could not bring the host to the state the release wants."""


def host_services(
    prod: Production, config: GlobalConfig, inherited_path: str
) -> list[HostService]:
    """The orchestrator and the agent daemon, as the supervisor runs them.

    The orchestrator serves plain HTTP on loopback. ``dev_host:`` adds a public
    listener, which speaks TLS under ``dev_https:``.
    """
    env = production_env(prod, inherited_path)
    orchestrator_env = dict(env)
    if config.tls_host is not None:
        cert, key = cert_paths(config.tls_host)
        orchestrator_env |= {"DEV_TLS_CERT": str(cert), "DEV_TLS_KEY": str(key)}
    public = ("--public-host", config.dev_host) if config.dev_host else ()
    return [
        HostService(
            name=ORCHESTRATOR,
            argv=(
                str(prod.bin / "mael-orchestrator"),
                "serve",
                "--host",
                "127.0.0.1",
                *public,
                "--port",
                str(PRODUCTION_ORCHESTRATOR_PORT),
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
            daemon_owns_children=True,
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
    """The host services, with the listeners the global config names."""
    return host_services(prod, load_config_file(prod.config), inherited_path)


def all_units(prod: Production, inherited_path: str) -> list[HostService]:
    """Every unit setup installs: the host services and the scheduler."""
    return [*configured_services(prod, inherited_path), scheduler(prod, inherited_path)]


def orchestrator_url(prod: Production) -> str:
    """The URL the production notebook's clients dial, from the global config."""
    return load_config_file(prod.config).derived_orchestrator_url


def restarts_for(changed: Sequence[str]) -> list[str]:
    """The host services the changed components restart, in a fixed order."""
    names = {name for c in changed for name in RESTARTED_BY.get(c, ())}
    return [n for n in (ORCHESTRATOR, AGENT_DAEMON) if n in names]


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
    #: ``None`` runs :meth:`plugins`' setup.
    install_plugins: Callable[[], list[str]] | None = None
    harden: Callable[[], list[str]] = harden_global_config
    link_jig: Callable[[Path, Echo], None] = link_jig
    #: Issues or renews the dev certificate for a host.
    issue_cert: Callable[[str], object] = ensure_dev_cert
    #: Whether a user can answer a question, so setup may ask one.
    interactive: bool = False
    prompt: Prompt = lambda text, default: default
    #: The host services that did not load or restart.
    failed: list[str] = field(default_factory=list)

    def __call__(self) -> None:
        self.move_old_config()
        self.global_config()
        self.relocate()
        self.migrate()
        self.echo("Updating Claude Code integration...")
        for msg in [
            *self.integrate(),
            *(self.install_plugins or self.plugins().setup)(),
            *self.harden(),
        ]:
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
        self.echo(f"Setup complete. Maelstrom runs at {orchestrator_url(self.prod)}.")

    def plugins(self) -> Plugins:
        """The host's plugins, probed and run on the agents' PATH."""
        path = agent_path(self.prod, self.inherited_path)
        return Plugins(
            home=self.prod.home,
            shared=get_shared_dir(),
            run=self.run,
            which=lambda name: shutil.which(name, path=path),
            codex_home=codex_home_from_env(),
        )

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

    def global_config(self) -> None:
        """Make sure ``~/.maelstrom/config.yaml`` names the projects directory.

        A fresh install has no global config. Setup asks for the directory
        only when the key is unset and a user can answer, so only the first
        setup that finds it unset asks.
        """
        config = self.prod.config
        value = read_config_key(config, PROJECTS_DIR_KEY)
        if isinstance(value, str) and value:
            projects = value
        else:
            if self.interactive:
                projects = self.prompt(
                    "Where do your projects live?", DEFAULT_PROJECTS_DIR
                )
            else:
                projects = DEFAULT_PROJECTS_DIR
                self.echo(
                    f"  {PROJECTS_DIR_KEY}: {projects}. To change it, edit {config}."
                )
            if not Path(projects).expanduser().is_absolute():
                raise SetupError(
                    f"{PROJECTS_DIR_KEY} {projects!r} is relative. Give a path "
                    "that starts with / or ~."
                )
            set_config_key(config, PROJECTS_DIR_KEY, _yaml_scalar(projects))
        Path(projects).expanduser().mkdir(parents=True, exist_ok=True)

    def migrate(self) -> None:
        """Migrate ``state.db``, delete the stored orchestrator URL, store the unit PATH."""
        self.echo("Migrating the state database...")
        asyncio.run(migrate_state_db(self.prod.state / "state.db"))
        stored = read_config_key(self.prod.config, ORCHESTRATOR_URL_KEY)
        if stored is not None:
            remove_config_key(self.prod.config, ORCHESTRATOR_URL_KEY)
            derived = orchestrator_url(self.prod)
            if str(stored).strip().rstrip("/") == derived:
                self.echo(f"  Removed {ORCHESTRATOR_URL_KEY}: it is derived now.")
            else:
                self.echo(
                    f"  Removed {ORCHESTRATOR_URL_KEY} {stored}. Maelstrom now runs "
                    f"at {derived}, from dev_host: and dev_https:."
                )
        stored = store_service_path(self.prod, self.inherited_path)
        if stored is not None:
            self.echo(f"  {SERVICE_PATH_KEY}: {stored}")

    def move_old_config(self) -> None:
        """Move the keys an older setup wrote to ``state/config.yaml`` up, once.

        Production reads only the global file, so the old one goes when nothing
        else is left in it. Anything else in it, and a ``~/.maelstrom.yaml``
        from before ``~/.maelstrom/``, is named for the user to move.
        """
        legacy = self.prod.home / LEGACY_CONFIG_FILENAME
        if legacy.is_file():
            self.echo(
                f"  Warning: {legacy} is no longer read. Move what it holds into "
                f"{self.prod.config}, then delete it."
            )
        old = self.prod.state / GLOBAL_CONFIG_FILENAME
        for key in (PROJECTS_DIR_KEY, ORCHESTRATOR_URL_KEY, SERVICE_PATH_KEY):
            value = read_config_key(old, key)
            if not isinstance(value, str):
                continue
            if read_config_key(self.prod.config, key) is None:
                set_config_key(self.prod.config, key, value)
                self.echo(f"  Moved {key} to {self.prod.config}")
            remove_config_key(old, key)
        if not old.is_file():
            return
        if not old.read_text().strip():
            old.unlink()
            return
        self.echo(
            f"  Warning: {old} is no longer read. Move what it holds into "
            f"{self.prod.config}, then delete it."
        )

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
        config = load_config_file(self.prod.config)
        renewed = self.renew_cert(config.tls_host)
        specs = host_services(self.prod, config, self.inherited_path)
        orchestrator = specs[0].env
        for var in ("DEV_TLS_CERT", "DEV_TLS_KEY"):
            if var in orchestrator and not Path(orchestrator[var]).is_file():
                self.echo(
                    f"  Warning: dev_https: is on, and {orchestrator[var]} is "
                    "missing, so the orchestrator cannot serve TLS."
                )
        applied = self.supervisor.apply(specs)
        wanted = restarts_for(sorted(changed))
        if renewed and ORCHESTRATOR not in wanted:
            wanted.insert(0, ORCHESTRATOR)
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

    def renew_cert(self, host: str | None) -> bool:
        """Issue or renew the dev certificate for ``host``, the TLS host.

        Returns whether the certificate changed, so the orchestrator restarts
        onto it. A failure is named and does not stop setup.
        """
        if host is None:
            return False
        cert, _ = cert_paths(host)
        before = cert.read_bytes() if cert.is_file() else None
        try:
            self.issue_cert(host)
        except DevCertError as exc:
            self.echo(f"  Warning: the dev certificate was not renewed. {exc}")
            return False
        after = cert.read_bytes() if cert.is_file() else None
        if after == before:
            return False
        self.echo(f"  Renewed the dev certificate for {host}.")
        return True

    def schedule(self) -> None:
        """Install the hourly scheduler. It is always on."""
        (self.prod.maelstrom / _OLD_SCHEDULE_MARKER).unlink(missing_ok=True)
        applied = self.supervisor.apply([scheduler(self.prod, self.inherited_path)])
        for msg in applied.messages:
            self.echo(f"  {msg}")
        self.failed += applied.failed
