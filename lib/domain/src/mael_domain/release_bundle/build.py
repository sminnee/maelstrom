"""Build a release bundle from a checkout: ``bin/build-bundle``.

CI runs it on a ``v*`` tag, and ``mael self-update --channel main`` runs
``_main``'s copy. See "Release bundle" in CONTEXT.md.
"""

import argparse
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from mael_domain.env import ServiceVersionError, service_versions
from mael_domain.release_bundle import install
from mael_domain.release_bundle.install import BUNDLE_DIR, COMPONENTS, Manifest

#: The wheel each Python component is, by its distribution's file-name prefix.
WHEELS = {
    "cli": "sminnee_maelstrom",
    "orchestrator": "mael_orchestrator",
    "agent-daemon": "mael_agent_daemon",
    "common": "mael_common",
    "agent": "mael_agent",
    "domain": "mael_domain",
}

UI = "orchestrator-ui"
TARBALL = "maelstrom.tar.gz"


class BuildError(RuntimeError):
    """The checkout cannot make a bundle."""


def build_wheels(repo: Path, out: Path) -> dict[str, Path]:
    """One wheel per workspace member in ``out``, by component name."""
    _run(["uv", "build", "--all-packages", "--wheel", "-o", str(out)], repo)
    built = {w.name.split("-", 1)[0]: w for w in out.glob("*.whl")}
    missing = [c for c, dist in WHEELS.items() if dist not in built]
    if missing:
        raise BuildError(f"uv built no wheel for {', '.join(missing)}")
    return {c: built[dist] for c, dist in WHEELS.items()}


def build_bundle(
    repo: Path, out: Path, tag: str | None = None, allow_dirty: bool = False
) -> Manifest:
    """Write the bundle of ``repo``'s ``HEAD`` to ``out``.

    A component's version is the hash of committed files, so a dirty tree
    would ship files its versions do not cover. ``allow_dirty`` builds anyway,
    marks the manifest ``dirty``, and stamps each version with the build time.
    The installer keeps a version it has, so an unstamped dirty version would
    hide the clean one, or be hidden by it.
    """
    commit = _output(["git", "rev-parse", "HEAD"], repo)
    dirty = bool(_output(["git", "status", "--porcelain"], repo))
    if dirty and not allow_dirty:
        raise BuildError(f"{repo} has uncommitted changes; pass --allow-dirty")
    stamp = dirty_stamp() if dirty else None
    if tag is None:
        tag = default_tag(commit, stamp)
    try:
        versions = service_versions(repo, list(COMPONENTS))
    except ServiceVersionError as exc:
        raise BuildError(str(exc)) from exc
    missing = [n for n in COMPONENTS if n not in versions]
    if missing:
        raise BuildError(f"pipeline.toml has no SHA bucket for {', '.join(missing)}")
    versions = {n: versions[n] + (stamp or "") for n in COMPONENTS}
    manifest = Manifest(tag=tag, commit=commit, dirty=dirty, versions=versions)

    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / BUNDLE_DIR
        components = bundle / "components"
        components.mkdir(parents=True)
        for name, wheel in build_wheels(repo, Path(tmp) / "wheels").items():
            (components / name).mkdir()
            shutil.copy2(wheel, components / name)
        _run(["pnpm", "--dir", UI, "build"], repo)
        shutil.copytree(repo / UI / "dist", components / "web")
        (components / "python-deps").mkdir()
        _run(
            [
                "uv",
                "export",
                "--frozen",
                "--no-dev",
                "--no-emit-workspace",
                "--quiet",
                "--format",
                "requirements-txt",
                "--output-file",
                str(components / "python-deps" / install.REQUIREMENTS),
            ],
            repo,
        )
        manifest.write(bundle / install.MANIFEST)
        shutil.copy2(install.__file__, bundle / "install.py")
        out.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(out, "w:gz") as tar:
            tar.add(bundle, arcname=BUNDLE_DIR)
    return manifest


def dirty_stamp() -> str:
    """``-dirty-<UTC time>``: two dirty builds of one commit differ."""
    return f"-dirty-{datetime.now(UTC):%Y%m%d%H%M%S}"


def default_tag(commit: str, stamp: str | None) -> str:
    """``dev-<commit>``, and the dirty build's stamp.

    The installer refuses a second set of versions under the tag that runs.
    """
    return f"dev-{commit[:12]}{stamp or ''}"


def _run(cmd: list[str], cwd: Path) -> None:
    try:
        subprocess.run(cmd, cwd=cwd, check=True)
    except FileNotFoundError as exc:
        raise BuildError(f"{cmd[0]} is not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise BuildError(f"{' '.join(cmd)} failed (exit {exc.returncode})") from exc


def _output(cmd: list[str], cwd: Path) -> str:
    return subprocess.run(
        cmd, cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a maelstrom release bundle.")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--tag", help="the release tag (default: dev-<commit>)")
    parser.add_argument("--out", type=Path, help=f"default: <repo>/dist/{TARBALL}")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="build from a tree with uncommitted changes",
    )
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    out = (args.out or repo / "dist" / TARBALL).resolve()
    try:
        manifest = build_bundle(repo, out, args.tag, args.allow_dirty)
    except BuildError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Built {manifest.tag}: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
