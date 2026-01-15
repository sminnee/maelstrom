"""Port allocation and availability checking for maelstrom worktrees."""

import socket
from pathlib import Path


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
        port_base: The 3-digit base (100-999). Ports will be port_base * 10 + suffix.
        num_ports: Number of ports to check (default 10, for suffixes 0-9).

    Returns:
        True if all ports in the range are free.
    """
    for suffix in range(num_ports):
        port = port_base * 10 + suffix
        if not is_port_free(port):
            return False
    return True


def allocate_port_base(project_path: Path, num_ports: int = 10) -> int:
    """Find the next available PORT_BASE where all ports in the range are free.

    Args:
        project_path: Path to the project (used for future tracking).
        num_ports: Number of ports needed per worktree (default 10).

    Returns:
        The first available PORT_BASE (100-999).

    Raises:
        RuntimeError: If no available port ranges are found.
    """
    for base in range(100, 1000):
        if check_ports_free(base, num_ports):
            return base
    raise RuntimeError("No available port ranges found (checked PORT_BASE 100-999)")


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
