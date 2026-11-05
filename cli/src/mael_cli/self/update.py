"""``mael self update``, and the jig link of a source install.

An update is ``bin/install.sh``: the release's own copy on the stable channel,
and ``_main``'s copy on the main channel. The installer ends by running the new
release's ``mael self setup``. See ``docs/dev/host-services.md``.
"""

import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from mael_domain.worktree_model import MAIN_WORKTREE_FOLDER

#: Where the stable channel's bundles and installers are published.
RELEASES = "https://github.com/sminnee/maelstrom/releases"
INSTALLER = "install.sh"

#: The project whose ``_main`` the main channel builds.
MAELSTROM_PROJECT = "maelstrom"

#: The jig's Vite plugin, which the main channel links.
JIG_PACKAGE = "vite-plugin-mael-jig"

Echo = Callable[[str], None]


def link_jig(package: Path, echo: Echo) -> None:
    """Build the checkout's jig plugin and register it with `pnpm link --global`.

    A project then runs `pnpm link --global vite-plugin-mael-jig` to use it in
    place of the npm release. Best-effort.
    """
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        echo("  Warning: 'pnpm' not found; skipping the jig plugin link.")
        return
    echo("Building and linking the jig plugin...")
    for step in (
        ["install", "--frozen-lockfile"],
        ["run", "build"],
        ["link", "--global"],
    ):
        result = subprocess.run(
            [pnpm, *step], cwd=package, capture_output=True, text=True
        )
        if result.returncode != 0:
            echo(result.stderr or result.stdout)
            echo(
                f"  Warning: 'pnpm {' '.join(step)}' failed; the jig plugin is not linked."
            )
            return


class UpdateError(RuntimeError):
    """The update did not start. Nothing changed."""


def installer_url(tag: str | None) -> str:
    """The ``install.sh`` of the release ``tag``, or of the latest release."""
    if tag is None:
        return f"{RELEASES}/latest/download/{INSTALLER}"
    return f"{RELEASES}/download/{tag}/{INSTALLER}"


def update_stable(
    tag: str | None, run: Callable[..., subprocess.CompletedProcess]
) -> int:
    """Download the release's ``install.sh`` and run it. Returns its exit code."""
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / INSTALLER
        url = installer_url(tag)
        fetched = run(["curl", "-LsSf", "-o", str(script), url])
        if fetched.returncode != 0:
            raise UpdateError(f"could not download {url}")
        return run(["sh", str(script), *(["--tag", tag] if tag else [])]).returncode


def main_checkout(projects_dir: Path) -> Path:
    """The ``_main`` checkout the main channel builds from."""
    return projects_dir / MAELSTROM_PROJECT / MAIN_WORKTREE_FOLDER


def update_main(main: Path, run: Callable[..., subprocess.CompletedProcess]) -> int:
    """Run ``_main``'s own ``install.sh --source``. Returns its exit code.

    ``_main``'s copy, not the installed one, so a change to the installer lands
    with the commit that makes it.
    """
    if not (main / ".git").exists():
        raise UpdateError(
            f"The main channel builds from {main}, which is not a checkout. "
            f"Run `mael project add sminnee/{MAELSTROM_PROJECT}` first."
        )
    return run(["sh", str(main / "bin" / INSTALLER), "--source", str(main)]).returncode
