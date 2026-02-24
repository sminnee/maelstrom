"""Port allocation and availability checking for maelstrom worktrees."""

import json
import socket
from pathlib import Path


ALLOCATIONS_FILENAME = "port_allocations.json"


def is_port_free(port: int) -> bool:
    """Check if a port is available by attempting to connect to it."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.1)  # Short timeout
            result = s.connect_ex(("127.0.0.1", port))
            # 0 = connection succeeded = something is listening = port NOT free
            # Non-zero (e.g., ECONNREFUSED) = nothing listening = port is free
            return result != 0
    except OSError:
        return True  # Error connecting means port is likely free


def check_ports_free(port_base: int, num_ports: int = 10) -> bool:
    """Check if all ports in the range are free.

    Args:
        port_base: The 3-digit base (300-999). Ports will be port_base * 10 + suffix.
        num_ports: Number of ports to check (default 10, for suffixes 0-9).

    Returns:
        True if all ports in the range are free.
    """
    for suffix in range(num_ports):
        port = port_base * 10 + suffix
        if not is_port_free(port):
            return False
    return True


def _get_allocations_path() -> Path:
    """Return the path to the port allocations JSON file."""
    from .context import get_maelstrom_dir

    return get_maelstrom_dir() / ALLOCATIONS_FILENAME


def load_port_allocations() -> dict[str, dict[str, int]]:
    """Load port allocations from ~/.maelstrom/port_allocations.json.

    Returns:
        Dict keyed by project path (str) -> dict of worktree_name -> port_base.
        Returns empty dict if file is missing or corrupt.
    """
    path = _get_allocations_path()
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_port_allocations(allocations: dict[str, dict[str, int]]) -> None:
    """Save port allocations to ~/.maelstrom/port_allocations.json."""
    path = _get_allocations_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(allocations, f, indent=2, sort_keys=True)


def get_allocated_port_bases(allocations: dict[str, dict[str, int]]) -> set[int]:
    """Extract all currently allocated port bases from the allocations dict."""
    bases = set()
    for project_worktrees in allocations.values():
        for port_base in project_worktrees.values():
            bases.add(port_base)
    return bases


def record_port_allocation(project_path: Path, worktree_name: str, port_base: int) -> None:
    """Record a port allocation for a worktree."""
    allocations = load_port_allocations()
    project_key = str(project_path.resolve())
    if project_key not in allocations:
        allocations[project_key] = {}
    allocations[project_key][worktree_name] = port_base
    save_port_allocations(allocations)


def remove_port_allocation(project_path: Path, worktree_name: str) -> None:
    """Remove a port allocation for a worktree."""
    allocations = load_port_allocations()
    project_key = str(project_path.resolve())
    if project_key in allocations and worktree_name in allocations[project_key]:
        del allocations[project_key][worktree_name]
        if not allocations[project_key]:
            del allocations[project_key]
        save_port_allocations(allocations)


def get_port_allocation(project_path: Path, worktree_name: str) -> int | None:
    """Get the existing port allocation for a worktree, if any."""
    allocations = load_port_allocations()
    project_key = str(project_path.resolve())
    return allocations.get(project_key, {}).get(worktree_name)


def allocate_port_base(project_path: Path, num_ports: int = 10) -> int:
    """Find the next available PORT_BASE where all ports in the range are free.

    Checks both persistent allocations (to avoid collisions with allocated-but-idle
    ports from other worktrees) and actual socket availability (to avoid conflicts
    with non-maelstrom services).

    Args:
        project_path: Path to the project.
        num_ports: Number of ports needed per worktree (default 10).

    Returns:
        The first available PORT_BASE (300-999).

    Raises:
        RuntimeError: If no available port ranges are found.
    """
    allocations = load_port_allocations()
    allocated_bases = get_allocated_port_bases(allocations)

    for base in range(300, 1000):
        if base in allocated_bases:
            continue
        if check_ports_free(base, num_ports):
            return base
    raise RuntimeError("No available port ranges found (checked PORT_BASE 300-999)")


def get_app_url(project_path: Path, worktree_name: str) -> tuple[str, bool] | None:
    """Get the app URL and running status for a worktree.

    Args:
        project_path: Path to the project.
        worktree_name: Name of the worktree (e.g., "alpha").

    Returns:
        Tuple of (url, is_running) e.g. ("http://localhost:3010", True),
        or None if no port allocation exists.
    """
    port_base = get_port_allocation(project_path, worktree_name)
    if port_base is None:
        return None
    port = port_base * 10
    url = f"http://localhost:{port}"
    is_running = not is_port_free(port)
    return (url, is_running)


def generate_port_env_vars(port_base: int, port_names: list[str]) -> dict[str, str]:
    """Generate environment variables for port assignments.

    Args:
        port_base: The 3-digit base (100-999).
        port_names: List of port names (e.g., ["FRONTEND", "SERVER", "DB"]).

    Returns:
        Dictionary of environment variables (e.g., {"FRONTEND_PORT": "1000", ...}).
    """
    env_vars = {"PORT_BASE": str(port_base)}
    for idx, name in enumerate(port_names):
        port = port_base * 10 + idx
        env_vars[f"{name}_PORT"] = str(port)
    return env_vars
