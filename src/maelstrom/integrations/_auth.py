"""Shared secret resolution for service integrations.

The three integrations resolve their API keys through the same chain:
environment variable → ``.env`` file walked upward from the cwd (continuing past
``.env`` files that lack the key) → the global ``~/.maelstrom/config.yaml``. The
``.env`` files are parsed with the shared ``parse_env_text`` so values match the
rest of the codebase. This module parameterizes that chain. It returns
``None`` when nothing is found — converting a missing key into a user-facing
``click.ClickException`` is the caller's job, so the help text stays per-service.
"""

import os
from pathlib import Path

from ..context import load_global_config
from ..worktree_model import parse_env_text


def resolve_secret(env_name: str, *, config_attr: str) -> str | None:
    """Resolve a secret from env var, ``.env`` walk, then global config.

    Args:
        env_name: The environment-variable / ``.env`` key (e.g. ``LINEAR_API_KEY``).
        config_attr: The attribute on the global config holding the fallback
            (e.g. ``linear_api_key``).

    Returns:
        The resolved value, or ``None`` if not found in any source.
    """
    if value := os.environ.get(env_name):
        return value

    # Walk up from the cwd, parsing each ``.env`` with the canonical parser.
    # Continue past ``.env`` files that lack the key — the walk only ends when
    # the key is found or we reach the filesystem root.
    current = Path.cwd()
    while current != current.parent:
        env_path = current / ".env"
        if env_path.exists():
            env_vars = parse_env_text(env_path.read_text())
            if env_name in env_vars:
                return env_vars[env_name]
        current = current.parent

    return getattr(load_global_config(), config_attr)
