"""Tests for ``target-guard.sh``, the target check of the ``/security-test`` skill.

The seam is the script's command line, run against a worktree ``.env`` built by
the production renderer: the guard is the one hard stop between an active ZAP
scan and a host that is not this worktree's.
"""

import subprocess
from pathlib import Path

import pytest

from mael_domain.worktree_model import render_managed_section

SCRIPT = Path(__file__).resolve().parents[1] / "skills/security-test/target-guard.sh"

DEV_HOST = "cobalt.example.ts.net"


def _env(tmp_path: Path, **values: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(
        render_managed_section(values) + "\nDB_URL=postgres://localhost:5432/app\n"
    )
    return path


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    return _env(
        tmp_path,
        DEV_HOST=DEV_HOST,
        PORT_BASE="359",
        FRONTEND_PORT="3590",
        FAKE_FRONTEND_PORT="3594",
        POSTGRES_PORT="4100",
    )


def _guard(url: str, env_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), url, str(env_file)], capture_output=True, text=True
    )


@pytest.mark.parametrize(
    "url",
    [
        f"https://{DEV_HOST}:3594",
        "http://localhost:3590",
        "http://127.0.0.1:3590",
        "http://host.docker.internal:3594",
    ],
)
def test_accepts_a_worktree_host_on_a_worktree_port(url: str, env_file: Path) -> None:
    result = _guard(url, env_file)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == url


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        (f"https://{DEV_HOST}:2770", "port 2770 is not a *_PORT"),
        (f"https://{DEV_HOST}:359", "port 359 is not a *_PORT"),
        (f"https://{DEV_HOST}:35940", "port 35940 is not a *_PORT"),
        ("http://localhost:5432", "port 5432 is not a *_PORT"),
        ("http://localhost:4100", "outside this worktree's PORT_BASE 359"),
        ("https://example.com:3594", "host example.com is not"),
        ("http://example.com@localhost:3590", "is not this worktree's DEV_HOST"),
        ("http://localhost:3590@example.com:3590", "is not scheme://host:port"),
        (f"https://{DEV_HOST}:3590/login", "is not scheme://host:port"),
        ("http://localhost:3590/\nhttp://other:3590", "is not scheme://host:port"),
        (f"https://{DEV_HOST}", "is not scheme://host:port"),
        (f"ftp://{DEV_HOST}:3594", "is not scheme://host:port"),
        (f"{DEV_HOST}:3594", "is not scheme://host:port"),
    ],
)
def test_refuses_every_other_target(url: str, reason: str, env_file: Path) -> None:
    result = _guard(url, env_file)
    assert result.returncode == 1
    assert reason in result.stderr
    assert result.stdout == ""


def test_refuses_a_remote_host_when_the_env_has_no_dev_host(tmp_path: Path) -> None:
    env_file = _env(tmp_path, PORT_BASE="359", FRONTEND_PORT="3590")
    result = _guard("https://anything.ts.net:3590", env_file)
    assert result.returncode == 1
    assert "DEV_HOST (unset)" in result.stderr


def test_refuses_a_port_when_the_env_has_no_port_base(tmp_path: Path) -> None:
    env_file = _env(tmp_path, DEV_HOST=DEV_HOST, FRONTEND_PORT="3590")
    result = _guard("http://localhost:3590", env_file)
    assert result.returncode == 1
    assert "PORT_BASE unset" in result.stderr


def test_refuses_when_the_env_file_is_missing(tmp_path: Path) -> None:
    result = _guard("http://localhost:3590", tmp_path / ".env")
    assert result.returncode == 1
    assert "refused: no" in result.stderr
