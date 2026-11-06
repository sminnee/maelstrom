"""``mael self update``, the **Update job**, and the jig link of a source install.

An update is ``bin/install.sh``: the release's own copy on the stable channel,
and ``_main``'s copy on the main channel. The installer ends by running the new
release's ``mael self setup``, which restarts the host services. An update that
must outlive that restart runs as an **Update job**: a one-shot unit of the OS
supervisor. See ``docs/dev/host-services.md``.
"""

import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from mael_domain.release_bundle.layout import ManifestError, current_manifest
from mael_domain.release_bundle.update_job import (
    UpdateRecord,
    lock_held,
    read_record,
    update_lock,
    write_record,
)
from mael_domain.worktree_model import MAIN_WORKTREE_FOLDER

from .host_service import HostService, Supervisor
from .production import Production, production_env

#: Where the stable channel's bundles and installers are published.
RELEASES = "https://github.com/sminnee/maelstrom/releases"
INSTALLER = "install.sh"

#: The bootstrap that lays out ``_main`` and installs from it.
INSTALL_MAIN_URL = (
    "https://raw.githubusercontent.com/sminnee/maelstrom/main/bin/install-main.sh"
)

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
            f"Make it with `curl -LsSf {INSTALL_MAIN_URL} | sh`."
        )
    return run(["sh", str(main / "bin" / INSTALLER), "--source", str(main)]).returncode


#: The one-shot unit's name: launchd label ``nz.tangerinelabs.maelstrom.update``,
#: systemd unit ``maelstrom-update``.
UPDATE_JOB = "update"
#: The job's plist, outside ``~/Library/LaunchAgents``. See :class:`HostService`.
UPDATE_PLIST = "update.plist"


def update_job_spec(
    prod: Production, inherited_path: str, tag: str | None
) -> HostService:
    """The one-shot unit that runs ``mael self update --as-job``.

    It runs the resolved release, not ``current``: the installer moves
    ``current`` during the run, and the old release stays on disk.
    """
    current = prod.install_root / "current"
    if not current.exists():
        raise UpdateError(
            f"No release is installed at {current}. Run `mael self update` "
            "without --detach."
        )
    mael = current.resolve() / "venv" / "bin" / "mael"
    return HostService(
        name=UPDATE_JOB,
        argv=(
            str(mael),
            "self",
            "update",
            "--as-job",
            *(["--tag", tag] if tag else []),
        ),
        env=production_env(prod, inherited_path),
        oneshot=True,
        plist=prod.install_root / UPDATE_PLIST,
        cwd=prod.maelstrom,
        log=prod.update_log,
    )


def submit_update(supervisor: Supervisor, spec: HostService, prod: Production) -> None:
    """Start the **Update job** ``spec``. Refuses while an update runs."""
    if lock_held(prod.install_root):
        record = read_record(prod.install_root)
        where = f" Follow it in {record.log}." if record else ""
        raise UpdateError(f"An update is running.{where}")
    supervisor.submit(spec)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _current_tag(install_root: Path) -> str | None:
    try:
        manifest = current_manifest(install_root)
    except (ManifestError, OSError, ValueError, KeyError):
        return None
    return None if manifest is None else manifest.tag


#: Runs the update with the given ``run``, and returns the installer's exit code.
Install = Callable[[Callable[..., subprocess.CompletedProcess]], int]


def run_as_job(
    install_root: Path,
    log: Path,
    install: Install,
    pid: int | None = None,
    now: Callable[[], str] = _now,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """The body of the **Update job**: run ``install``, and record it.

    The update lock holds for the whole run. A job that finds another update
    holding it writes no record and installs nothing. Returns the exit code.
    """
    pid = os.getpid() if pid is None else pid
    log.parent.mkdir(parents=True, exist_ok=True)
    with update_lock(install_root, sleep) as locked, open(log, "a") as out:
        if not locked:
            out.write("Another update is running; this job stops.\n")
            return 1
        # The log holds this run alone. It is emptied only under the lock, so
        # a refused job cannot empty the log of the one that runs.
        out.truncate(0)
        started = UpdateRecord(
            state="running",
            pid=pid,
            started_at=now(),
            log=str(log),
            from_tag=_current_tag(install_root),
        )
        write_record(install_root, started)
        out.write(f"Update started at {started.started_at} (pid {pid}).\n")
        out.flush()

        def run(argv, **_kwargs) -> subprocess.CompletedProcess:
            out.flush()
            return subprocess.run(
                argv, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT
            )

        try:
            code = install(run)
        except UpdateError as exc:
            out.write(f"{exc}\n")
            code = 1
        out.write(f"Update ended with exit {code}.\n")
        write_record(
            install_root,
            UpdateRecord(
                state="succeeded" if code == 0 else "failed",
                pid=pid,
                started_at=started.started_at,
                log=str(log),
                from_tag=started.from_tag,
                to_tag=_current_tag(install_root),
                finished_at=now(),
                exit_code=code,
            ),
        )
        return code
