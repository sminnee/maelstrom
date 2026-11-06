"""The layout of a release bundle and of the install root.

See "Release bundle" and "Component" in CONTEXT.md, and
``docs/adr/0002-one-installer-and-host-services.md``.

The layout under the install root::

    components/<name>/<version>/   one directory per component version
    releases/<tag>/manifest.toml   the versions one release uses
    releases/<tag>/venv/           the release's Python environment
    current -> releases/<tag>      the release that runs

``bin/install.sh`` writes it. This module is the Python side that reads it:
``bin/build-bundle`` writes the manifest, ``mael self status`` reads it, and the
orchestrator finds its ``web`` component through it.
"""

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

#: The components of a ``host`` bundle, each the name of a SHA bucket in
#: ``pipeline.toml``. ``bin/install.sh`` holds the same list.
COMPONENTS = (
    "cli",
    "orchestrator",
    "agent-daemon",
    "web",
    "common",
    "agent",
    "domain",
    "python-deps",
)

FORMAT = 1
KIND = "host"
MANIFEST = "manifest.toml"
#: The directory a bundle's tarball unpacks to.
BUNDLE_DIR = "maelstrom"
REQUIREMENTS = "requirements.txt"

#: A tag or a version is a path segment under the install root.
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class ManifestError(RuntimeError):
    """A manifest is not one this build can read."""


@dataclass(frozen=True)
class Manifest:
    """What a bundle holds: its tag, its commit, and each component's version.

    The format is fixed and flat, because ``bin/install.sh`` reads it with
    ``awk``: one ``key = value`` per line, and one ``[components.<name>]``
    table per component.
    """

    tag: str
    commit: str
    dirty: bool
    versions: dict[str, str] = field(default_factory=dict)
    kind: str = KIND

    @classmethod
    def read(cls, path: Path) -> "Manifest":
        data = tomllib.loads(path.read_text())
        if data.get("format") != FORMAT:
            raise ManifestError(
                f"{path}: manifest format {data.get('format')!r}, expected {FORMAT}"
            )
        manifest = cls(
            tag=data["tag"],
            commit=data["commit"],
            dirty=data["dirty"],
            versions={
                name: table["version"]
                for name, table in data.get("components", {}).items()
            },
            kind=data["kind"],
        )
        for value in (manifest.tag, *manifest.versions.values()):
            if not _SEGMENT.fullmatch(value):
                raise ManifestError(f"{path}: {value!r} is not a tag or a version")
        return manifest

    def dumps(self) -> str:
        # Every value is a plain string or a bool, so a JSON string is a valid
        # TOML basic string, and the stdlib has no TOML writer.
        lines = [
            f"format = {FORMAT}",
            f"kind = {json.dumps(self.kind)}",
            f"tag = {json.dumps(self.tag)}",
            f"commit = {json.dumps(self.commit)}",
            f"dirty = {'true' if self.dirty else 'false'}",
        ]
        for name, version in self.versions.items():
            lines += ["", f"[components.{name}]", f"version = {json.dumps(version)}"]
        return "\n".join(lines) + "\n"

    def write(self, path: Path) -> None:
        path.write_text(self.dumps())


def current_manifest(root: Path) -> Manifest | None:
    """The manifest of the release that runs, or ``None`` before the first install."""
    path = root / "current" / MANIFEST
    return Manifest.read(path) if path.is_file() else None


def running_manifest(prefix: str) -> Manifest | None:
    """The manifest of the release whose venv is ``prefix``.

    ``prefix`` is ``sys.prefix``. ``None`` outside an installed release, such
    as a worktree's ``.venv``.
    """
    path = Path(prefix).resolve().parent / MANIFEST
    return Manifest.read(path) if path.is_file() else None


def installed_web_root(prefix: str) -> Path | None:
    """The ``web`` component of the release whose venv is ``prefix``, or ``None``."""
    manifest = running_manifest(prefix)
    version = None if manifest is None else manifest.versions.get("web")
    if version is None:
        return None
    web = Path(prefix).resolve().parents[2] / "components" / "web" / version
    return web if (web / "index.html").is_file() else None
