"""The install status: the shell wrapper writes it, the readers parse it."""

import subprocess
import threading
from pathlib import Path

import pytest

from mael_domain import install_status
from mael_domain.install_status import (
    install_argv,
    install_log_path,
    mark_running,
    names_from_path,
    read_install_status,
    wait_for_install,
)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    return tmp_path / "home"


def status_file() -> Path:
    return install_log_path("proj", "bravo").with_name("install.status")


def test_an_exit_in_the_command_still_records_its_code():
    subprocess.run(install_argv("proj", "bravo", "echo hi; exit 3"), check=False)

    status = read_install_status("proj", "bravo")
    assert status is not None and status.code == 3


def test_tee_sends_the_output_to_the_log_and_stdout():
    result = subprocess.run(
        install_argv("proj", "bravo", "echo hi; echo oops >&2", tee=True),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.stdout.split() == ["hi", "oops"]
    assert install_log_path("proj", "bravo").read_text().split() == ["hi", "oops"]
    status = read_install_status("proj", "bravo")
    assert status is not None and status.passed


@pytest.mark.parametrize(
    ("text", "code"),
    [("0\n", 0), ("2\n", 2), ("running 1\n", None), ("running 999999999\n", 1)],
)
def test_read_install_status(text, code):
    # PID 1 is alive on every host. 999999999 is above any pid_max.
    status_file().parent.mkdir(parents=True)
    status_file().write_text(text)

    status = read_install_status("proj", "bravo")

    assert status is not None and status.code == code


@pytest.mark.parametrize("text", [None, "", "garbage\n", "running\n"])
def test_no_or_unreadable_status_is_none(text):
    if text is not None:
        status_file().parent.mkdir(parents=True)
        status_file().write_text(text)

    assert read_install_status("proj", "bravo") is None


def test_wait_returns_when_the_running_install_ends(monkeypatch):
    monkeypatch.setattr(install_status, "POLL_INTERVAL", 0.01)
    mark_running("proj", "bravo", 1)
    timer = threading.Timer(0.1, lambda: status_file().write_text("0\n"))
    timer.start()

    status = wait_for_install("proj", "bravo")

    timer.join()
    assert status is not None and status.passed


@pytest.mark.parametrize(
    ("path", "names"),
    [
        ("/p/askastro/askastro-bravo", ("askastro", "bravo")),
        ("/p/askastro/_main", ("askastro", "_main")),
        ("/p/askastro/other", ("askastro", "other")),
    ],
)
def test_names_from_path(path, names):
    assert names_from_path(Path(path)) == names
