"""Install a release bundle into ``~/.maelstrom/install/``.

See "Release bundle" and "Component" in CONTEXT.md, and
``docs/adr/0001-maelstrom-installs-as-a-release-bundle.md``.

The layout under the install root::

    components/<name>/<version>/   one directory per component version
    releases/<tag>/manifest.toml   the versions one release uses
    releases/<tag>/venv/           the release's Python environment
    current -> releases/<tag>      the release that runs

This module imports the standard library only. ``bin/build-bundle`` copies it
to the bundle root as ``install.py``, and a new host runs that copy before any
venv exists.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

#: The components of a ``host`` bundle, each the name of a SHA bucket in
#: ``pipeline.toml``.
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

#: Where a host installs, unless told otherwise.
DEFAULT_ROOT = Path.home() / ".maelstrom" / "install"


class InstallError(RuntimeError):
    """The bundle cannot be installed. Nothing that runs has changed."""


@dataclass(frozen=True)
class Manifest:
    """What a bundle holds: its tag, its commit, and each component's version."""

    tag: str
    commit: str
    dirty: bool
    versions: dict[str, str] = field(default_factory=dict)
    kind: str = KIND

    @classmethod
    def read(cls, path: Path) -> "Manifest":
        data = tomllib.loads(path.read_text())
        if data.get("format") != FORMAT:
            raise InstallError(
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
                raise InstallError(f"{path}: {value!r} is not a tag or a version")
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


@dataclass(frozen=True)
class InstallReport:
    """What an install changed, against the release that was ``current``.

    A first install changes every component. A rollback can change components
    whose versions were all on disk already.
    """

    tag: str
    changed: list[str]
    unchanged: list[str]


#: Builds a venv at its final path: shebangs name it, so it cannot move.
BuildVenv = Callable[[Path, Path, list[Path]], None]


def uv_venv(venv: Path, requirements: Path, wheels: list[Path]) -> None:
    """A uv venv of the pinned requirements, then the wheels without resolving.

    The venv uses the interpreter that runs the installer. uv's cache makes a
    rebuild cheap, so a venv is not shared between releases.
    """
    uv = shutil.which("uv")
    if uv is None:
        raise InstallError("uv is not on PATH")
    python = venv / "bin" / "python"
    for cmd in (
        [uv, "venv", "--quiet", "--python", sys.executable, str(venv)],
        [uv, "pip", "install", "--quiet", "--python", str(python)]
        + ["--require-hashes", "-r", str(requirements)],
        [uv, "pip", "install", "--quiet", "--python", str(python), "--no-deps"]
        + [str(w) for w in wheels],
    ):
        subprocess.run(cmd, check=True)


def install_bundle(
    tarball: Path, root: Path = DEFAULT_ROOT, build_venv: BuildVenv = uv_venv
) -> InstallReport:
    """Unpack ``tarball`` and install it, as :func:`install_tree` does."""
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root, prefix=".unpack-") as unpacked:
        with tarfile.open(tarball) as tar:
            tar.extractall(unpacked, filter="data")
        return install_tree(Path(unpacked) / BUNDLE_DIR, root, build_venv)


def install_tree(
    bundle: Path, root: Path = DEFAULT_ROOT, build_venv: BuildVenv = uv_venv
) -> InstallReport:
    """Install the unpacked bundle at ``bundle``, then make it ``current``.

    Only the last step moves ``current``, so a failure before it leaves
    ``current`` where it was.
    """
    manifest = Manifest.read(bundle / MANIFEST)
    missing = [n for n in COMPONENTS if n not in manifest.versions]
    if missing:
        raise InstallError(f"the bundle has no {', '.join(missing)} component")
    components = root / "components"
    for name in COMPONENTS:
        _copy_component(
            bundle / "components" / name, components / name, manifest.versions[name]
        )

    before = _current_versions(root)
    release = root / "releases" / manifest.tag
    _prepare_release(release, manifest, root)
    venv = release / "venv"
    if not (venv / "bin" / "python").exists():
        dirs = [components / n / manifest.versions[n] for n in COMPONENTS]
        try:
            build_venv(
                venv,
                components
                / "python-deps"
                / manifest.versions["python-deps"]
                / REQUIREMENTS,
                sorted(w for d in dirs for w in d.glob("*.whl")),
            )
        except BaseException:
            # A half-built venv has a python, so the next try would take it
            # for a finished one.
            shutil.rmtree(venv, ignore_errors=True)
            raise
    _point_current(root, release)
    changed = [n for n in COMPONENTS if before.get(n) != manifest.versions[n]]
    return InstallReport(
        tag=manifest.tag,
        changed=changed,
        unchanged=[n for n in COMPONENTS if n not in changed],
    )


def _copy_component(source: Path, parent: Path, version: str) -> None:
    """Copy one component version, unless the host has it already."""
    target = parent / version
    if target.is_dir():
        return
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=parent, prefix=f".{version}.tmp-"))
    try:
        shutil.copytree(source, staging, dirs_exist_ok=True)
        os.replace(staging, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def current_manifest(root: Path) -> Manifest | None:
    """The manifest of the release that runs, or ``None`` before the first install."""
    path = root / "current" / MANIFEST
    return Manifest.read(path) if path.is_file() else None


def _current_versions(root: Path) -> dict[str, str]:
    current = current_manifest(root)
    return current.versions if current else {}


def _prepare_release(release: Path, manifest: Manifest, root: Path) -> None:
    """Give ``release`` the manifest, keeping its venv when the versions match.

    A tag installed with other versions is a dirty rebuild under one tag. Its
    release is rebuilt, or refused when it is the release that runs now.
    """
    path = release / MANIFEST
    if path.is_file():
        if Manifest.read(path).versions == manifest.versions:
            return
        if (root / "current").resolve() == release.resolve():
            raise InstallError(
                f"release {manifest.tag} runs now with other component versions; "
                "build the bundle under a new tag"
            )
        shutil.rmtree(release)
    release.mkdir(parents=True)
    manifest.write(path)


def _point_current(root: Path, release: Path) -> None:
    """Move ``current`` to ``release`` with one rename."""
    link = root / "current"
    staging = root / f".current.tmp-{os.getpid()}"
    staging.unlink(missing_ok=True)
    staging.symlink_to(release.relative_to(root))
    os.replace(staging, link)


def installed_web_root(prefix: str) -> Path | None:
    """The ``web`` component of the release whose venv is ``prefix``.

    ``prefix`` is ``sys.prefix``. ``None`` outside an installed release, such
    as a worktree's ``.venv``.
    """
    venv = Path(prefix).resolve()
    path = venv.parent / MANIFEST
    if not path.is_file():
        return None
    version = Manifest.read(path).versions.get("web")
    if version is None:
        return None
    web = venv.parents[2] / "components" / "web" / version
    return web if (web / "index.html").is_file() else None


def format_report(report: InstallReport) -> list[str]:
    """The lines that tell the user what an install changed."""
    if not report.changed:
        return [f"{report.tag} is installed; no component changed."]
    lines = [f"Installed {report.tag}. Changed: {', '.join(report.changed)}."]
    if report.unchanged:
        lines.append(f"Unchanged: {', '.join(report.unchanged)}.")
    return lines


def main(argv: list[str] | None = None) -> int:
    """``python install.py [BUNDLE] [--root ROOT]``.

    Without ``BUNDLE``, installs the unpacked bundle around this file.
    """
    parser = argparse.ArgumentParser(description="Install a maelstrom release bundle.")
    parser.add_argument("bundle", nargs="?", type=Path, help="maelstrom.tar.gz")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)
    try:
        if args.bundle is not None:
            report = install_bundle(args.bundle, args.root)
        else:
            report = install_tree(Path(__file__).resolve().parent, args.root)
    except (InstallError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    for line in format_report(report):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
