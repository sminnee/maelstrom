"""What the Welcome dialog shows about this install: its paths, skills and version.

Read on each request, because ``mael self setup`` can link a skill or install
a release while the server runs.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mael_common.util import GLOBAL_CONFIG_DIR

from .release_bundle.layout import ManifestError, running_manifest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SystemPaths:
    """The directories one install reads. A test gives its own temp dirs."""

    home: Path
    config_dir: Path
    projects_dir: Path
    claude_skills_dir: Path
    #: ``sys.prefix`` of the running code: its release names the version.
    prefix: str

    @classmethod
    def for_home(cls, home: Path, projects_dir: Path, prefix: str) -> "SystemPaths":
        """The paths of the install under ``home``, as ``mael self setup`` lays them out."""
        return cls(
            home=home,
            config_dir=home / GLOBAL_CONFIG_DIR,
            projects_dir=projects_dir,
            claude_skills_dir=home / ".claude" / "skills",
            prefix=prefix,
        )


def linked_skills(claude_skills_dir: Path) -> list[str]:
    """The names in ``claude_skills_dir`` that are live links into a ``shared/skills``.

    ``mael self setup`` writes these links, into a release or a checkout. A real
    directory or a link to somewhere else is the user's own skill, and a dead
    link is no skill at all.
    """
    if not claude_skills_dir.is_dir():
        return []
    names = []
    for entry in claude_skills_dir.iterdir():
        if not entry.is_symlink() or not entry.exists():
            continue
        parent = entry.resolve().parent
        if (parent.parent.name, parent.name) == ("shared", "skills"):
            names.append(entry.name)
    return sorted(names)


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
        "skillsDir": str(paths.claude_skills_dir),
        "skills": linked_skills(paths.claude_skills_dir),
    }
