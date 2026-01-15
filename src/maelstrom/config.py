"""Configuration loading for maelstrom projects."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


CONFIG_FILENAME = ".maelstrom.yaml"


@dataclass
class MaelstromConfig:
    """Configuration for a maelstrom-managed project."""

    port_names: list[str] = field(default_factory=list)
    start_cmd: str = ""
    install_cmd: str = ""
    # Linear integration
    linear_team_id: str | None = None
    linear_workspace_labels: list[str] | None = None
    # Sentry integration
    sentry_org: str | None = None
    sentry_project: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "MaelstromConfig":
        """Create a config from a dictionary."""
        return cls(
            port_names=data.get("port_names", []),
            start_cmd=data.get("start_cmd", ""),
            install_cmd=data.get("install_cmd", ""),
            linear_team_id=data.get("linear_team_id"),
            linear_workspace_labels=data.get("linear_workspace_labels"),
            sentry_org=data.get("sentry_org"),
            sentry_project=data.get("sentry_project"),
        )


def find_config_file(path: Path) -> Path | None:
    """Find .maelstrom.yaml starting from the given path and searching upward.

    Args:
        path: Starting path (file or directory).

    Returns:
        Path to the config file, or None if not found.
    """
    if path.is_file():
        path = path.parent

    current = path.resolve()
    while current != current.parent:
        config_path = current / CONFIG_FILENAME
        if config_path.exists():
            return config_path
        current = current.parent

    return None


def load_config(worktree_path: Path) -> MaelstromConfig:
    """Load .maelstrom.yaml configuration from a worktree.

    Args:
        worktree_path: Path to the worktree directory.

    Returns:
        MaelstromConfig with the loaded configuration.

    Raises:
        FileNotFoundError: If no .maelstrom.yaml is found.
        yaml.YAMLError: If the YAML is invalid.
    """
    config_file = find_config_file(worktree_path)
    if config_file is None:
        raise FileNotFoundError(
            f"No {CONFIG_FILENAME} found in {worktree_path} or parent directories"
        )

    with open(config_file) as f:
        data = yaml.safe_load(f) or {}

    return MaelstromConfig.from_dict(data)


def load_config_or_default(worktree_path: Path) -> MaelstromConfig:
    """Load config or return default if not found.

    Args:
        worktree_path: Path to the worktree directory.

    Returns:
        MaelstromConfig with loaded or default configuration.
    """
    try:
        return load_config(worktree_path)
    except FileNotFoundError:
        return MaelstromConfig()
