"""Shared secret resolution for service integrations.

The three integrations resolve their API keys through the same chain:
environment variable → ``.env`` file walked upward from the cwd → the global
``~/.maelstrom/config.yaml``. This module parameterizes that chain. It returns
``None`` when nothing is found — converting a missing key into a user-facing
``click.ClickException`` is the caller's job, so the help text stays per-service.
"""

import os
import re
from pathlib import Path

from ..context import load_global_config


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

    # Find .env file in current directory or parents
    current = Path.cwd()
    while current != current.parent:
        env_path = current / ".env"
        if env_path.exists():
            content = env_path.read_text()
            pattern = rf"^{re.escape(env_name)}\s*=\s*[\"']?([^\"'\n]+)[\"']?"
            if match := re.search(pattern, content, re.MULTILINE):
                return match.group(1)
            break
        current = current.parent

    return getattr(load_global_config(), config_attr)


def get_env_var(name: str) -> str | None:
    """Resolve an env var from ``os.environ`` or an upward ``.env`` walk.

    Returns ``None`` when the variable is not set anywhere; unlike
    :func:`resolve_secret` there is no global-config fallback.
    """
    if value := os.environ.get(name):
        return value

    current = Path.cwd()
    while current != current.parent:
        env_path = current / ".env"
        if env_path.exists():
            content = env_path.read_text()
            pattern = rf"^{re.escape(name)}\s*=\s*[\"']?([^\"'\n]+)[\"']?"
            if match := re.search(pattern, content, re.MULTILINE):
                return match.group(1)
            break
        current = current.parent

    return None
