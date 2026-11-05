"""Smoke tests: each ``mael self`` command runs on a tmp home.

The supervisor and the commands it would run are fakes, so nothing reaches
launchd or systemd. ``tests/test_release_bundle.py`` installs a real bundle.
"""

import subprocess
from dataclasses import dataclass, field

import pytest
from click.testing import CliRunner, Result

from mael_cli.cli import cli
from mael_cli.self.host_service import Applied, ServiceStatus


@dataclass
class FakeSupervisor:
    calls: list[str] = field(default_factory=list)

    def _record(self, verb, specs):
        self.calls.append(f"{verb} {' '.join(s.name for s in specs)}")

    def apply(self, specs):
        self._record("apply", specs)
        return Applied(started=[s.name for s in specs])

    def remove(self, specs):
        self._record("remove", specs)
        return []

    def restart(self, specs):
        self._record("restart", specs)
        return Applied()

    def status(self, specs):
        return [ServiceStatus(s.name, loaded=True, pid=7) for s in specs]

    def log_hint(self, spec):
        return f"log of {spec.name}"


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
    assert "Setup complete." in result.output


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
