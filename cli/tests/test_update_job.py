"""The **Update job**: its unit, its submit to each supervisor, and its run."""

import fcntl
import plistlib
import subprocess

import pytest

from mael_cli.self.host_service import Launchd, SubmitError, Systemd, render_plist
from mael_cli.self.production import Production
from mael_cli.self.update import (
    UpdateError,
    run_as_job,
    submit_update,
    update_job_spec,
)
from mael_domain.release_bundle.layout import MANIFEST, Manifest
from mael_domain.release_bundle.update_job import (
    UpdateRecord,
    lock_path,
    read_record,
    update_lock,
    write_record,
)


def _install(prod: Production, tag: str) -> None:
    """Install release ``tag`` and point ``current`` at it."""
    release = prod.install_root / "releases" / tag
    release.mkdir(parents=True)
    Manifest(tag=tag, commit="abc", dirty=False).write(release / MANIFEST)
    current = prod.install_root / "current"
    current.unlink(missing_ok=True)
    current.symlink_to(release)


@pytest.fixture
def prod(tmp_path):
    prod = Production(tmp_path)
    _install(prod, "v1")
    return prod


@pytest.fixture
def spec(prod):
    return update_job_spec(prod, "/usr/bin", tag=None)


class Recorder:
    """A ``Run`` that records each argv; ``launchctl print`` finds nothing loaded.

    ``replies`` answers a command, by its first two words, with an exit code
    and its stdout.
    """

    def __init__(self, replies: dict[str, tuple[int, str]] | None = None):
        self.calls: list[list[str]] = []
        self.replies = {"launchctl print": (1, ""), **(replies or {})}

    def __call__(self, argv):
        self.calls.append(argv)
        code, out = self.replies.get(" ".join(argv[:2]), (0, ""))
        return subprocess.CompletedProcess(argv, code, out, "")

    def verbs(self) -> list[str]:
        return [" ".join(a[:2]) for a in self.calls if a[:2] != ["launchctl", "print"]]


def test_the_job_runs_the_resolved_release_not_current(prod, spec):
    release = (prod.install_root / "releases" / "v1").resolve()

    assert spec.argv == (
        str(release / "venv" / "bin" / "mael"),
        "self",
        "update",
        "--as-job",
    )
    assert spec.env["MAEL_PRODUCTION"] == "1"


def test_a_tag_reaches_the_job(prod):
    spec = update_job_spec(prod, "/usr/bin", tag="v0.1.0")

    assert spec.argv[-2:] == ("--tag", "v0.1.0")


def test_no_release_installed_refuses(tmp_path):
    with pytest.raises(UpdateError, match="No release is installed"):
        update_job_spec(Production(tmp_path), "/usr/bin", tag=None)


def test_a_one_shot_plist_runs_once_and_is_not_kept_alive(spec):
    plist = plistlib.loads(render_plist(spec))

    assert plist["RunAtLoad"] is True
    assert "KeepAlive" not in plist
    assert "StartCalendarInterval" not in plist


def test_launchd_boots_out_the_old_job_then_loads_the_plist_outside_launch_agents(
    prod, spec
):
    run = Recorder()

    Launchd(run=run, home=prod.home, uid=501, sleep=lambda _: None).submit(spec)

    plist = prod.install_root / "update.plist"
    assert plist.is_file()
    assert not (prod.home / "Library" / "LaunchAgents").exists()
    assert [a for a in run.calls if a[:2] != ["launchctl", "print"]] == [
        ["launchctl", "bootout", "gui/501/nz.tangerinelabs.maelstrom.update"],
        ["launchctl", "bootstrap", "gui/501", str(plist)],
    ]


def test_launchd_refuses_while_the_job_runs(prod, spec):
    """bootout would kill it mid-install."""
    run = Recorder({"launchctl print": (0, "\tpid = 812\n")})

    with pytest.raises(SubmitError, match="pid 812"):
        Launchd(run=run, home=prod.home, uid=501, sleep=lambda _: None).submit(spec)

    assert run.verbs() == []


def test_launchd_reports_a_refused_bootstrap(prod, spec):
    run = Recorder({"launchctl bootstrap": (5, "")})

    with pytest.raises(SubmitError, match="bootstrap failed"):
        Launchd(run=run, home=prod.home, uid=501, sleep=lambda _: None).submit(spec)


def test_systemd_runs_a_transient_unit_that_collects_itself(prod, spec):
    run = Recorder()

    Systemd(run=run, home=prod.home, user="sam").submit(spec)

    (systemd_run,) = run.calls
    assert systemd_run[:4] == [
        "systemd-run",
        "--user",
        "--unit=maelstrom-update",
        "--collect",
    ]
    assert "--setenv=MAEL_PRODUCTION=1" in systemd_run
    assert f"--property=WorkingDirectory={prod.maelstrom}" in systemd_run
    assert f"--property=StandardOutput=append:{prod.update_log}" in systemd_run
    tail = systemd_run[systemd_run.index("--") + 1 :]
    assert tail == list(spec.argv)


def test_systemd_reports_a_refused_unit(prod, spec):
    run = Recorder({"systemd-run --user": (1, "")})

    with pytest.raises(SubmitError, match="systemd-run failed"):
        Systemd(run=run, home=prod.home, user="sam").submit(spec)


class Submits:
    def __init__(self):
        self.specs = []

    def submit(self, spec):
        self.specs.append(spec)


def test_submit_refuses_while_an_update_holds_the_lock(prod, spec):
    write_record(
        prod.install_root,
        UpdateRecord("running", 1, "t0", str(prod.update_log)),
    )
    sup = Submits()

    with update_lock(prod.install_root):
        with pytest.raises(UpdateError, match="An update is running"):
            submit_update(sup, spec, prod)
    assert sup.specs == []


def test_submit_after_an_interrupted_job_starts_a_new_one(prod, spec):
    write_record(
        prod.install_root,
        UpdateRecord("running", 1, "t0", str(prod.update_log)),
    )
    sup = Submits()

    submit_update(sup, spec, prod)

    assert sup.specs == [spec]


def _job(prod: Production, install) -> int:
    return run_as_job(
        prod.install_root, prod.update_log, install, pid=4242, now=lambda: "t"
    )


def test_the_job_records_running_then_its_result(prod):
    seen = []

    def install(run):
        seen.append(read_record(prod.install_root))
        _install(prod, "v2")
        return 0

    assert _job(prod, install) == 0

    assert seen == [
        UpdateRecord(
            state="running",
            pid=4242,
            started_at="t",
            log=str(prod.update_log),
            from_tag="v1",
        )
    ]
    assert read_record(prod.install_root) == UpdateRecord(
        state="succeeded",
        pid=4242,
        started_at="t",
        finished_at="t",
        log=str(prod.update_log),
        from_tag="v1",
        to_tag="v2",
        exit_code=0,
    )


def test_a_failed_install_records_its_exit_code(prod):
    assert _job(prod, lambda run: 3) == 3

    record = read_record(prod.install_root)
    assert (record.state, record.exit_code, record.to_tag) == ("failed", 3, "v1")


def test_an_update_that_cannot_start_records_a_failure(prod):
    def install(run):
        raise UpdateError("no _main checkout")

    assert _job(prod, install) == 1

    assert read_record(prod.install_root).state == "failed"
    assert "no _main checkout" in prod.update_log.read_text()


def test_the_log_holds_this_run_alone(prod):
    prod.update_log.parent.mkdir(parents=True)
    prod.update_log.write_text("the last run\n")

    def install(run):
        return run(["sh", "-c", "echo from-the-installer; exit 0"]).returncode

    assert _job(prod, install) == 0

    log = prod.update_log.read_text()
    assert "from-the-installer" in log
    assert "the last run" not in log


def test_a_second_job_waits_for_no_lock_and_installs_nothing(prod):
    installed = []
    prod.update_log.parent.mkdir(parents=True)
    prod.update_log.write_text("the running job's log\n")
    with open(lock_path(prod.install_root), "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)

        code = run_as_job(
            prod.install_root,
            prod.update_log,
            lambda run: installed.append(run) or 0,
            sleep=lambda _: None,
        )

    assert code == 1
    assert installed == []
    assert read_record(prod.install_root) is None
    assert "the running job's log" in prod.update_log.read_text()
