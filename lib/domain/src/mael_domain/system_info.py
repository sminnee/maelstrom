"""What the Welcome dialog shows about this install: its paths, skills, version and update.

Read on each request, because a user can install a plugin, ``mael self
setup`` can install a release, and an **Update job** writes its record, while
the server runs.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mael_agent.harness_model import PRACTICES_PLUGIN
from mael_common.util import GLOBAL_CONFIG_DIR, read_json

from .context import GLOBAL_CONFIG_FILENAME, load_config_file
from .release_bundle.layout import (
    INSTALL_DIRNAME,
    Manifest,
    ManifestError,
    running_manifest,
)
from .release_bundle.update_job import read_record
from .shared_dir import core_plugin_dir

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SystemPaths:
    """The directories one install reads. A test gives its own temp dirs."""

    home: Path
    config_dir: Path
    projects_dir: Path
    #: The skills of the core plugin this code launches agents with.
    core_skills_dir: Path
    #: Where Claude Code records the plugins a user installed.
    claude_plugins_dir: Path
    #: ``sys.prefix`` of the running code: its release names the version.
    prefix: str
    #: Where releases install, and where the **Update job** writes its record.
    install_root: Path
    #: Whether the server runs as a host service (``MAEL_PRODUCTION=1``).
    production: bool = False

    @classmethod
    def for_home(
        cls, home: Path, projects_dir: Path, prefix: str, production: bool = False
    ) -> "SystemPaths":
        """The paths of the install under ``home``, as ``mael self setup`` lays them out."""
        plugin = core_plugin_dir()
        config_dir = home / GLOBAL_CONFIG_DIR
        return cls(
            home=home,
            config_dir=config_dir,
            projects_dir=projects_dir,
            # A tree with no core plugin lists no skills.
            core_skills_dir=(plugin or home / "missing-core-plugin") / "skills",
            claude_plugins_dir=home / ".claude" / "plugins",
            prefix=prefix,
            install_root=config_dir / INSTALL_DIRNAME,
            production=production,
        )


def plugin_skills(skills_dir: Path) -> list[str]:
    """The skill names in a plugin's ``skills/`` dir: each dir with a ``SKILL.md``."""
    if not skills_dir.is_dir():
        return []
    return sorted(p.name for p in skills_dir.iterdir() if (p / "SKILL.md").is_file())


def practices_installed(claude_plugins_dir: Path) -> bool:
    """Whether Claude Code lists ``minnstack`` as installed."""
    installed = read_json(claude_plugins_dir / "installed_plugins.json").get("plugins")
    return isinstance(installed, dict) and PRACTICES_PLUGIN in installed


def _manifest(paths: SystemPaths) -> Manifest | None:
    try:
        return running_manifest(paths.prefix)
    except (ManifestError, OSError, ValueError, KeyError):
        # ValueError covers a TOML syntax error, KeyError a missing field.
        logger.warning(
            "Cannot read the release manifest beside %s", paths.prefix, exc_info=True
        )
        return None


def can_update(paths: SystemPaths) -> bool:
    """Whether this server may start an **Update job**.

    Only an installed release that runs as a host service may. A server run
    from a checkout, or without ``MAEL_PRODUCTION=1``, would update the
    production install, not itself.
    """
    return paths.production and _manifest(paths) is not None


def system_reply(paths: SystemPaths) -> dict[str, Any]:
    """The ``GET /api/system`` body. ``version`` is ``None`` for a run from a checkout."""
    manifest = _manifest(paths)
    version = (
        None
        if manifest is None
        else {"tag": manifest.tag, "commit": manifest.commit, "dirty": manifest.dirty}
    )
    record = read_record(paths.install_root)
    return {
        "version": version,
        "home": str(paths.home),
        "configDir": str(paths.config_dir),
        "projectsDir": str(paths.projects_dir),
        "skillsDir": str(paths.core_skills_dir),
        "skills": plugin_skills(paths.core_skills_dir),
        "practices": practices_installed(paths.claude_plugins_dir),
        "update": None if record is None else record.to_wire(),
        "updateChannel": load_config_file(
            paths.config_dir / GLOBAL_CONFIG_FILENAME
        ).update_channel,
        "canUpdate": paths.production and manifest is not None,
    }
