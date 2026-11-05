"""The production layout under one home directory, its environment, and the shim.

The host service units and the ``mael`` shim share one production
environment. See ``docs/dev/host-services.md``.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from mael_agent.agent_transport import ROOT_ENV
from mael_domain.context import GLOBAL_CONFIG_FILENAME, read_config_key, set_config_key
from mael_domain.notebook_root import NOTEBOOK_ROOT_ENV

#: The PATH of the units, stored in the global config by the first setup. A setup from another shell then writes the same unit files,
#: and restarts nothing.
SERVICE_PATH_KEY = "service_path"

#: Marks a `mael` that setup wrote, so the user can tell it from uv's.
SHIM_MARKER = "# maelstrom shim"


@dataclass(frozen=True)
class Production:
    """The paths of the production install under one home directory."""

    home: Path

    @property
    def maelstrom(self) -> Path:
        return self.home / ".maelstrom"

    @property
    def config(self) -> Path:
        """The global config: production's own keys live here too."""
        return self.maelstrom / GLOBAL_CONFIG_FILENAME

    @property
    def install_root(self) -> Path:
        return self.maelstrom / "install"

    @property
    def state(self) -> Path:
        """The production notebook root."""
        return self.maelstrom / "state"

    @property
    def daemon_root(self) -> Path:
        """The production daemon root."""
        return self.state / "daemon"

    @property
    def bin(self) -> Path:
        """The venv ``bin`` of the release that runs."""
        return self.install_root / "current" / "venv" / "bin"

    @property
    def service_logs(self) -> Path:
        return self.maelstrom / "logs" / "service"

    @property
    def shim(self) -> Path:
        """The `mael` on PATH: ``~/.local/bin/mael``, where uv puts tool entry points."""
        return self.home / ".local" / "bin" / "mael"


def agent_path(prod: Production, inherited: str) -> str:
    """The PATH of a host service: the shim, the caller's PATH, then the usual.

    A supervised process has a bare environment, and the agents it runs need
    ``claude``, ``git``, ``uv`` and the rest. The caller's PATH has them. A venv
    ``bin`` is dropped, so a worktree's ``mael`` never shadows the shim.
    """
    candidates = [
        str(prod.shim.parent),
        *(p for p in inherited.split(os.pathsep) if p and not _is_venv_bin(p)),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ]
    return os.pathsep.join(dict.fromkeys(candidates))


def _is_venv_bin(path: str) -> bool:
    return (Path(path).parent / "pyvenv.cfg").is_file()


def service_path(prod: Production, inherited: str) -> str:
    """The stored unit PATH, or the one :func:`agent_path` gives when none is stored."""
    stored = read_config_key(prod.config, SERVICE_PATH_KEY)
    return stored if isinstance(stored, str) and stored else agent_path(prod, inherited)


def store_service_path(prod: Production, inherited: str) -> str | None:
    """Store the unit PATH when none is stored. Returns the PATH it stored."""
    if read_config_key(prod.config, SERVICE_PATH_KEY):
        return None
    path = agent_path(prod, inherited)
    set_config_key(prod.config, SERVICE_PATH_KEY, path)
    return path


def production_env(prod: Production, inherited_path: str) -> dict[str, str]:
    """The environment every host service and the scheduler run with."""
    return {
        ROOT_ENV: str(prod.daemon_root),
        NOTEBOOK_ROOT_ENV: str(prod.state),
        "MAEL_PRODUCTION": "1",
        "PATH": service_path(prod, inherited_path),
    }


def write_shim(prod: Production) -> str:
    """Point the `mael` on PATH at the ``current`` release, with its roots.

    ``current`` is a symlink, so the shim does not change between releases.

    The shim supplies the production roots only when nothing else has: a
    worktree's `uv run mael` and a command inside a driven session both arrive
    with a root already set, and overriding either would send it to the wrong
    daemon. It keeps the caller's PATH.

    The write goes through a temporary file and a rename. The rename replaces
    a symlink rather than writing through it, and a running `mael` keeps its
    file.

    Best-effort: the release is installed by this point, so a failed write
    warns rather than aborting. Returns what to tell the user.
    """
    path = prod.shim
    mael = prod.bin / "mael"
    shim = (
        "#!/bin/sh\n"
        f"{SHIM_MARKER} — written by `mael self setup`.\n"
        "# The production roots, unless something already named one.\n"
        f'export {ROOT_ENV}="${{{ROOT_ENV}:-{prod.daemon_root}}}"\n'
        f'export {NOTEBOOK_ROOT_ENV}="${{{NOTEBOOK_ROOT_ENV}:-{prod.state}}}"\n'
        "export MAEL_PRODUCTION=1\n"
        f'exec "{mael}" "$@"\n'
    )
    staging = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        staging.write_text(shim)
        staging.chmod(0o755)
        os.replace(staging, path)
    except OSError:
        staging.unlink(missing_ok=True)
        return f"Warning: could not write {path}. Run {mael} with {ROOT_ENV} set."
    return f"`mael` at {path} runs the current release"
