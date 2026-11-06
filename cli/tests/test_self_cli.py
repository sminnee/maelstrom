"""Smoke tests: each ``mael self`` command runs on a tmp home.

The supervisor and the commands it would run are fakes, so nothing reaches
launchd or systemd. ``tests/test_release_bundle.py`` installs a real bundle.
"""

import os
import sqlite3
import subprocess
import zipfile

import pytest
from click.testing import CliRunner, Result
from self_cli_support import FakeSupervisor

from mael_cli.cli import cli
from mael_cli.self.production import Production, agent_path


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".maelstrom").mkdir()
    (tmp_path / ".maelstrom" / "config.yaml").write_text(
        f"projects_dir: {tmp_path / 'Projects'}\n"
    )
    sup = FakeSupervisor()
    monkeypatch.setattr("mael_cli.self.self_cli.supervisor", lambda run, home: sup)
    # tailscale is absent, so setup picks localhost.
    monkeypatch.setattr(
        "mael_cli.self.self_cli.run_quietly",
        lambda argv: subprocess.CompletedProcess(argv, 1, "", ""),
    )
    calls: list[str] = []

    def run(cmd, **_):
        calls.append(" ".join(map(str, cmd)))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("mael_cli.self.self_cli.subprocess.run", run)
    return sup, calls, tmp_path


def mael(*args: str) -> Result:
    return CliRunner().invoke(cli, ["self", *args])


def test_setup(host):
    sup, _, home = host

    result = mael("setup", "--changed", "orchestrator")

    assert result.exit_code == 0, result.output
    assert (home / ".maelstrom" / "state" / "state.db").exists()
    assert (home / ".local" / "bin" / "mael").exists()
    assert sup.calls == ["apply orchestrator agent-daemon", "apply schedule"]
    assert result.output.rstrip().endswith(
        "Setup complete. Maelstrom runs at http://localhost:2772."
    )


def test_check_host_without_a_tty_probes_the_agents_path(host, monkeypatch):
    _, _, home = host
    probed: set[str] = set()

    def which(name, path):
        probed.add(path)

    monkeypatch.setattr("mael_cli.self.self_cli.shutil.which", which)
    # The command puts the agents' PATH in its environment: undo it afterwards.
    caller_path = os.environ["PATH"]
    monkeypatch.setenv("PATH", caller_path)

    result = mael("check-host")

    assert result.exit_code == 1
    assert "gh (required) is missing" in result.output
    assert probed == {agent_path(Production(home), caller_path)}


def test_update(host):
    _, calls, _ = host

    result = mael("update", "--tag", "v0.2.0")

    assert result.exit_code == 0, result.output
    fetch, run = calls
    assert fetch.endswith("/releases/download/v0.2.0/install.sh")
    assert run.endswith(" --tag v0.2.0")


def test_status(host):
    result = mael("status")

    assert result.exit_code == 0, result.output
    assert "Release: none installed" in result.output
    assert "  orchestrator: loaded, pid 7" in result.output


def test_uninstall(host):
    sup, _, _ = host

    result = mael("uninstall")

    assert result.exit_code == 0, result.output
    assert sup.calls == ["remove orchestrator agent-daemon schedule"]


def test_restart(host):
    sup, _, _ = host

    result = mael("restart", "agent-daemon")

    assert result.exit_code == 0, result.output
    assert sup.calls == ["restart agent-daemon"]


@pytest.mark.parametrize(
    "command", ["self-update", "self-env", "install", "schedule", "admin"]
)
def test_the_old_host_commands_are_gone(command):
    result = CliRunner().invoke(cli, [command, "--help"])

    assert "No such command" in result.output


@pytest.fixture
def exported(host, tmp_path, monkeypatch):
    """`mael self export` run in ``tmp_path`` on a host with one wiki page."""
    assert mael("setup").exit_code == 0
    db = host[2] / ".maelstrom" / "state" / "state.db"
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO wiki_pages (id, revision) VALUES ('p', 1)")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("mael_cli.self.self_cli.socket.gethostname", lambda: "old.lan")
    return mael("export")


def test_export(exported, tmp_path):
    assert exported.exit_code == 0, exported.output
    (archive,) = tmp_path.glob("maelstrom-old-*.zip")
    assert exported.output == (
        f"Wrote {archive}: {archive.stat().st_size:,} bytes, 0 attachments.\n"
    )


def test_export_refusal_exits_1(exported, tmp_path):
    (archive,) = tmp_path.glob("maelstrom-old-*.zip")

    result = mael("export", str(archive))

    assert result.exit_code == 1
    assert "exists" in result.output


def test_import_with_replace(exported, tmp_path, host):
    (archive,) = tmp_path.glob("maelstrom-old-*.zip")

    result = mael("import", "--replace", str(archive))

    assert result.exit_code == 0, result.output
    assert list((host[2] / ".maelstrom" / "state").glob("replaced-*"))


def test_import_refusal_exits_1(host, tmp_path):
    archive = tmp_path / "x.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("notes.txt", "x")

    result = mael("import", str(archive))

    assert result.exit_code == 1
    assert "has no manifest.json" in result.output
