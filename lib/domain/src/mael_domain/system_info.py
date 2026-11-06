"""What the Welcome dialog shows about this install: its paths, skills and version.

Read on each request, because a user can install a plugin, and ``mael self
setup`` can install a release, while the server runs.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mael_agent.harness_model import PRACTICES_PLUGIN
from mael_common.util import GLOBAL_CONFIG_DIR, read_json

from .release_bundle.layout import ManifestError, running_manifest
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

    @classmethod
    def for_home(cls, home: Path, projects_dir: Path, prefix: str) -> "SystemPaths":
        """The paths of the install under ``home``, as ``mael self setup`` lays them out."""
        plugin = core_plugin_dir()
        return cls(
            home=home,
            config_dir=home / GLOBAL_CONFIG_DIR,
            projects_dir=projects_dir,
            # A tree with no core plugin lists no skills.
            core_skills_dir=(plugin or home / "missing-core-plugin") / "skills",
            claude_plugins_dir=home / ".claude" / "plugins",
            prefix=prefix,
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


def system_reply(paths: SystemPaths) -> dict[str, Any]:
    """The ``GET /api/system`` body. ``version`` is ``None`` for a run from a checkout."""
    try:
        manifest = running_manifest(paths.prefix)
    except (ManifestError, OSError, ValueError, KeyError):
        # ValueError covers a TOML syntax error, KeyError a missing field.
        logger.warning(
            "Cannot read the release manifest beside %s", paths.prefix, exc_info=True
        )
        manifest = None
    version = (
        None
        if manifest is None
        else {"tag": manifest.tag, "commit": manifest.commit, "dirty": manifest.dirty}
    )
    return {
        "version": version,
        "home": str(paths.home),
        "configDir": str(paths.config_dir),
        "projectsDir": str(paths.projects_dir),
        "skillsDir": str(paths.core_skills_dir),
        "skills": plugin_skills(paths.core_skills_dir),
        "practices": practices_installed(paths.claude_plugins_dir),
    }
