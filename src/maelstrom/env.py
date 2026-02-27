"""Process lifecycle engine for maelstrom environments.

Manages starting, stopping, and monitoring development services
defined in Procfiles or via start_cmd configuration.
"""

import json
import os
import signal
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from subprocess import STDOUT, Popen

from maelstrom.config import load_config_or_default
from maelstrom.context import get_maelstrom_dir
from maelstrom.worktree import read_env_file, run_install_cmd


# --- Dataclasses ---


@dataclass
class ProcfileEntry:
    """A single service entry from a Procfile."""

    name: str
    command: str


@dataclass
class ServiceState:
    """Persisted state of a running service."""

    name: str
    command: str
    pid: int
    log_file: str
    started_at: str  # ISO 8601


@dataclass
class EnvState:
    """Persisted state of a running environment."""

    project: str
    worktree: str
    worktree_path: str
    started_at: str  # ISO 8601
    services: list[ServiceState]


@dataclass
class SharedEnvState:
    """Persisted state of shared services for a project."""

    project: str
    worktree_path: str  # cwd of the worktree that started them
    started_at: str  # ISO 8601
    services: list[ServiceState]
    subscribers: list[str]  # worktree names currently using these


@dataclass
class ServiceStatus:
    """Live status of a service (state + liveness check)."""

    name: str
    pid: int
    alive: bool
    command: str
    log_file: str
    started_at: str


# --- Helpers ---


def is_shared_service(name: str) -> bool:
    """Return True if the service name indicates a shared service."""
    return name.endswith("-shared")


# --- Procfile Parsing ---


def parse_procfile(procfile_path: Path) -> list[ProcfileEntry]:
    """Parse a standard Procfile into service entries.

    Format: `name: command` per line. Comments (#) and empty lines are skipped.
    Splits on the first colon only, so commands may contain colons.

    Raises:
        FileNotFoundError: If the Procfile doesn't exist.
        ValueError: If a non-empty, non-comment line has no colon.
    """
    entries = []
    for line in procfile_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"Invalid Procfile line (no colon): {line!r}")
        name, command = line.split(":", 1)
        name = name.strip()
        command = command.strip()
        if not name:
            raise ValueError(f"Invalid Procfile line (empty name): {line!r}")
        entries.append(ProcfileEntry(name=name, command=command))
    return entries


def get_services(worktree_path: Path) -> list[ProcfileEntry]:
    """Get service definitions for a worktree.

    Checks for a Procfile first; falls back to config start_cmd as a
    single 'app' service. Raises RuntimeError if neither is available.
    """
    procfile = worktree_path / "Procfile"
    if procfile.exists():
        return parse_procfile(procfile)

    config = load_config_or_default(worktree_path)
    if config.start_cmd:
        return [ProcfileEntry(name="app", command=config.start_cmd)]

    raise RuntimeError(
        f"No Procfile found in {worktree_path} and no start_cmd configured"
    )


# --- State File Persistence ---


def get_state_dir() -> Path:
    """Return the directory for environment state files."""
    return get_maelstrom_dir() / "envs"


def _get_state_path(project: str, worktree: str) -> Path:
    """Return the path to a specific environment state file."""
    return get_state_dir() / project / f"{worktree}.json"


def _get_log_dir(project: str, worktree: str) -> Path:
    """Return the directory for service log files."""
    return get_maelstrom_dir() / "logs" / project / worktree


def load_env_state(project: str, worktree: str) -> EnvState | None:
    """Load environment state from disk.

    Returns None if the state file is missing or corrupt.
    """
    path = _get_state_path(project, worktree)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return EnvState(
            project=data["project"],
            worktree=data["worktree"],
            worktree_path=data["worktree_path"],
            started_at=data["started_at"],
            services=[ServiceState(**s) for s in data["services"]],
        )
    except (json.JSONDecodeError, KeyError, TypeError, OSError):
        return None


def save_env_state(state: EnvState) -> None:
    """Write environment state to disk."""
    path = _get_state_path(state.project, state.worktree)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(asdict(state), f, indent=2, sort_keys=True)


def remove_env_state(project: str, worktree: str) -> None:
    """Delete an environment state file if it exists."""
    path = _get_state_path(project, worktree)
    if path.exists():
        path.unlink()


# --- Shared State Persistence ---

SHARED_STATE_FILENAME = "_shared.json"


def _get_shared_state_path(project: str) -> Path:
    """Return the path to the shared services state file."""
    return get_state_dir() / project / SHARED_STATE_FILENAME


def _get_shared_log_dir(project: str) -> Path:
    """Return the directory for shared service log files."""
    return get_maelstrom_dir() / "logs" / project / "_shared"


def load_shared_state(project: str) -> SharedEnvState | None:
    """Load shared services state from disk.

    Returns None if the state file is missing or corrupt.
    """
    path = _get_shared_state_path(project)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return SharedEnvState(
            project=data["project"],
            worktree_path=data["worktree_path"],
            started_at=data["started_at"],
            services=[ServiceState(**s) for s in data["services"]],
            subscribers=data["subscribers"],
        )
    except (json.JSONDecodeError, KeyError, TypeError, OSError):
        return None


def save_shared_state(state: SharedEnvState) -> None:
    """Write shared services state to disk."""
    path = _get_shared_state_path(state.project)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(asdict(state), f, indent=2, sort_keys=True)


def remove_shared_state(project: str) -> None:
    """Delete the shared services state file if it exists."""
    path = _get_shared_state_path(project)
    if path.exists():
        path.unlink()


# --- Environment Building & Liveness ---


def build_service_env(worktree_path: Path) -> dict[str, str]:
    """Build the environment dict for spawned services.

    Starts with the current process environment and overlays
    variables from the worktree's .env file.
    """
    env = os.environ.copy()
    env.update(read_env_file(worktree_path))
    return env


def is_service_alive(pid: int) -> bool:
    """Check if a process is alive using signal 0.

    Returns True if the process exists (even if we lack permission to signal it).
    """
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


# --- Start / Stop / Status ---


def _spawn_services(
    services: list[ProcfileEntry],
    cwd: Path,
    env: dict[str, str],
    log_dir: Path,
    now: str,
) -> list[ServiceState]:
    """Spawn a list of services and return their states.

    Each service is started via ``sh -c`` in a new session, with
    stdout/stderr redirected to a log file.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    service_states = []

    for svc in services:
        log_file = log_dir / f"{svc.name}.log"
        log_fh = open(log_file, "w")  # noqa: SIM115
        log_fh.write(f"\n=== Service started: {now} ===\n")
        log_fh.flush()
        proc = Popen(
            ["sh", "-c", svc.command],
            cwd=cwd,
            env=env,
            stdout=log_fh,
            stderr=STDOUT,
            start_new_session=True,
        )
        log_fh.close()
        service_states.append(
            ServiceState(
                name=svc.name,
                command=svc.command,
                pid=proc.pid,
                log_file=str(log_file),
                started_at=now,
            )
        )

    return service_states


def _start_or_subscribe_shared(
    project: str,
    worktree: str,
    worktree_path: Path,
    shared_services: list[ProcfileEntry],
    env: dict[str, str],
    now: str,
) -> None:
    """Start shared services if not running, or subscribe to existing ones."""
    if not shared_services:
        return

    cleanup_stale_shared(project)
    shared_state = load_shared_state(project)

    if shared_state is not None:
        # Shared services already running — just subscribe
        if worktree not in shared_state.subscribers:
            shared_state.subscribers.append(worktree)
            save_shared_state(shared_state)
        return

    # Start shared services
    log_dir = _get_shared_log_dir(project)
    service_states = _spawn_services(
        shared_services, worktree_path, env, log_dir, now,
    )

    shared_state = SharedEnvState(
        project=project,
        worktree_path=str(worktree_path),
        started_at=now,
        services=service_states,
        subscribers=[worktree],
    )
    save_shared_state(shared_state)


def start_env(
    project: str,
    worktree: str,
    worktree_path: Path,
    *,
    skip_install: bool = False,
) -> EnvState:
    """Start all services for a worktree environment.

    1. Cleans up stale state
    2. Refuses to start if services are already running
    3. Runs install_cmd (unless skip_install)
    4. Splits services into local and shared
    5. Starts or subscribes to shared services
    6. Spawns local services with stdout/stderr to log files
    7. Saves and returns state (local services only)

    Raises:
        RuntimeError: If services are already running, or no services defined.
    """
    cleanup_stale_env(project, worktree)

    status = get_env_status(project, worktree)
    if status is not None:
        alive = [s for s in status if s.alive]
        if alive:
            names = ", ".join(s.name for s in alive)
            raise RuntimeError(
                f"Services already running for {project}/{worktree}: {names}"
            )

    if not skip_install:
        run_install_cmd(worktree_path)

    all_services = get_services(worktree_path)
    local_services = [s for s in all_services if not is_shared_service(s.name)]
    shared_services = [s for s in all_services if is_shared_service(s.name)]

    env = build_service_env(worktree_path)
    now = datetime.now(timezone.utc).isoformat()

    # Handle shared services
    _start_or_subscribe_shared(
        project, worktree, worktree_path, shared_services, env, now,
    )

    # Start local services
    log_dir = _get_log_dir(project, worktree)
    service_states = _spawn_services(
        local_services, worktree_path, env, log_dir, now,
    )

    state = EnvState(
        project=project,
        worktree=worktree,
        worktree_path=str(worktree_path),
        started_at=now,
        services=service_states,
    )
    save_env_state(state)
    return state


def _stop_services(
    services: list[ServiceState], *, timeout: float = 10.0, label: str = "",
) -> list[str]:
    """Send SIGTERM, wait, then SIGKILL to a list of services.

    Returns a list of status messages per service.
    """
    stop_time = datetime.now(timezone.utc).isoformat()

    # Write stop marker to log files
    for svc in services:
        try:
            with open(svc.log_file, "a") as f:
                f.write(f"\n=== Service stopped: {stop_time} ===\n")
        except OSError:
            pass

    # Send SIGTERM to each process group
    for svc in services:
        try:
            os.killpg(svc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    # Poll until all dead or timeout
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(not is_service_alive(s.pid) for s in services):
            break
        time.sleep(0.1)

    # SIGKILL survivors
    tag = f" ({label})" if label else ""
    messages = []
    for svc in services:
        if is_service_alive(svc.pid):
            try:
                os.killpg(svc.pid, signal.SIGKILL)
                messages.append(f"{svc.name}{tag} (pid {svc.pid}): killed (SIGKILL)")
            except (ProcessLookupError, PermissionError):
                messages.append(f"{svc.name}{tag} (pid {svc.pid}): stopped")
        else:
            messages.append(f"{svc.name}{tag} (pid {svc.pid}): stopped")

    return messages


def _unsubscribe_shared(
    project: str, worktree: str, *, timeout: float = 10.0,
) -> list[str]:
    """Unsubscribe a worktree from shared services.

    If this was the last subscriber, stops the shared services.
    Returns a list of status messages.
    """
    shared_state = load_shared_state(project)
    if shared_state is None:
        return []

    if worktree not in shared_state.subscribers:
        return []

    shared_state.subscribers.remove(worktree)

    if shared_state.subscribers:
        # Other worktrees still using shared services
        save_shared_state(shared_state)
        remaining = len(shared_state.subscribers)
        return [f"Shared services still used by {remaining} other environment(s)"]

    # Last subscriber — stop shared services
    messages = _stop_services(
        shared_state.services, timeout=timeout, label="shared",
    )
    remove_shared_state(project)
    return messages


def stop_env(
    project: str, worktree: str, *, timeout: float = 10.0
) -> list[str]:
    """Stop all services for a worktree environment.

    Sends SIGTERM to each process group, waits up to `timeout` seconds,
    then sends SIGKILL to survivors. Removes the state file afterwards.
    Also unsubscribes from shared services (stopping them if last subscriber).

    Returns a list of status messages per service.
    """
    state = load_env_state(project, worktree)
    if state is None:
        # Still try to unsubscribe from shared services
        shared_msgs = _unsubscribe_shared(project, worktree, timeout=timeout)
        if not shared_msgs:
            return [f"No running environment for {project}/{worktree}"]
        return shared_msgs

    messages = _stop_services(state.services, timeout=timeout)
    remove_env_state(project, worktree)

    # Handle shared services
    shared_msgs = _unsubscribe_shared(project, worktree, timeout=timeout)
    messages.extend(shared_msgs)

    return messages


def get_env_status(
    project: str, worktree: str
) -> list[ServiceStatus] | None:
    """Get the live status of all services in an environment.

    Returns None if no state file exists.
    """
    state = load_env_state(project, worktree)
    if state is None:
        return None

    return [
        ServiceStatus(
            name=svc.name,
            pid=svc.pid,
            alive=is_service_alive(svc.pid),
            command=svc.command,
            log_file=svc.log_file,
            started_at=svc.started_at,
        )
        for svc in state.services
    ]


def cleanup_stale_env(project: str, worktree: str) -> bool:
    """Remove state file if all tracked processes are dead.

    Returns True if stale state was cleaned up, False otherwise.
    """
    status = get_env_status(project, worktree)
    if status is None:
        return False

    if all(not s.alive for s in status):
        remove_env_state(project, worktree)
        return True

    return False


def get_shared_status(project: str) -> list[ServiceStatus] | None:
    """Get the live status of shared services for a project.

    Returns None if no shared state file exists.
    """
    state = load_shared_state(project)
    if state is None:
        return None

    return [
        ServiceStatus(
            name=svc.name,
            pid=svc.pid,
            alive=is_service_alive(svc.pid),
            command=svc.command,
            log_file=svc.log_file,
            started_at=svc.started_at,
        )
        for svc in state.services
    ]


def cleanup_stale_shared(project: str) -> bool:
    """Remove shared state if all tracked shared processes are dead.

    Returns True if stale state was cleaned up, False otherwise.
    """
    state = load_shared_state(project)
    if state is None:
        return False

    if all(not is_service_alive(s.pid) for s in state.services):
        remove_shared_state(project)
        return True

    return False


# --- Listing & Utilities ---


def list_project_envs(project: str) -> list[EnvState]:
    """List all running environments for a project.

    Iterates state files in ~/.maelstrom/envs/<project>/, cleans up stale
    entries, and returns the remaining live states.
    """
    project_dir = get_state_dir() / project
    if not project_dir.is_dir():
        return []

    results = []
    for state_file in sorted(project_dir.glob("*.json")):
        if state_file.name == SHARED_STATE_FILENAME:
            continue
        worktree = state_file.stem
        cleanup_stale_env(project, worktree)
        state = load_env_state(project, worktree)
        if state is not None:
            results.append(state)
    return results


def list_all_envs() -> list[EnvState]:
    """List all running environments across all projects."""
    state_dir = get_state_dir()
    if not state_dir.is_dir():
        return []

    results = []
    for project_dir in sorted(state_dir.iterdir()):
        if project_dir.is_dir():
            results.extend(list_project_envs(project_dir.name))
    return results


def stop_all_envs(*, timeout: float = 10.0) -> list[tuple[str, str, list[str]]]:
    """Stop all running environments across all projects.

    Returns a list of (project, worktree, messages) tuples.
    """
    results = []
    for state in list_all_envs():
        messages = stop_env(state.project, state.worktree, timeout=timeout)
        results.append((state.project, state.worktree, messages))
    return results


def get_log_files(project: str, worktree: str) -> dict[str, Path]:
    """Get log file paths for an environment's services.

    First tries loading state to get paths from ServiceState.log_file,
    then falls back to scanning the log directory for *.log files.
    Returns {service_name: log_file_path}, empty dict if nothing found.
    """
    state = load_env_state(project, worktree)
    if state is not None:
        result = {}
        for svc in state.services:
            path = Path(svc.log_file)
            if path.exists():
                result[svc.name] = path
        if result:
            return result

    # Fallback: scan log directory
    log_dir = _get_log_dir(project, worktree)
    if not log_dir.is_dir():
        return {}
    return {p.stem: p for p in sorted(log_dir.glob("*.log"))}


def tail_log_file(log_path: Path, n: int = 100) -> list[str]:
    """Read the last N lines from a log file.

    Returns empty list if the file is missing, empty, or unreadable.
    """
    try:
        lines = log_path.read_text().splitlines()
        return lines[-n:] if lines else []
    except OSError:
        return []


def read_service_logs(
    project: str,
    worktree: str,
    service: str | None = None,
    n: int = 100,
) -> list[tuple[str, str]]:
    """Read log lines for one or all services.

    Returns list of (service_name, line) tuples.
    If service is specified, reads only that service's log.
    If service is None, reads all services grouped by service.

    Raises:
        ValueError: If no logs found or service not recognized.
    """
    log_files = get_log_files(project, worktree)
    if not log_files:
        raise ValueError(f"No logs found for {project}/{worktree}")

    if service is not None:
        if service not in log_files:
            available = ", ".join(sorted(log_files.keys()))
            raise ValueError(
                f"Service '{service}' not found. Available: {available}"
            )
        lines = tail_log_file(log_files[service], n)
        return [(service, line) for line in lines]

    result: list[tuple[str, str]] = []
    for svc_name, log_path in sorted(log_files.items()):
        lines = tail_log_file(log_path, n)
        result.extend((svc_name, line) for line in lines)
    return result


def format_uptime(started_at: str) -> str:
    """Format a human-readable uptime string from an ISO 8601 timestamp.

    Examples: "5m", "2h 30m", "3d 5h".
    """
    start = datetime.fromisoformat(started_at)
    now = datetime.now(timezone.utc)
    delta = now - start
    total_seconds = int(delta.total_seconds())

    if total_seconds < 0:
        return "0s"

    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60

    if days > 0:
        if hours > 0:
            return f"{days}d {hours}h"
        return f"{days}d"
    if hours > 0:
        if minutes > 0:
            return f"{hours}h {minutes}m"
        return f"{hours}h"
    if minutes > 0:
        return f"{minutes}m"
    return f"{total_seconds}s"
