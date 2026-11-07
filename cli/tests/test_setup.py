"""``Setup`` on a tmp home: the global config, and the keys an older setup left.

Every outside effect is a fake. ``test_self_cli.py`` runs the command itself.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml
from self_cli_support import FakeSupervisor

from mael_cli.self.host_service import ORCHESTRATOR
from mael_cli.self.production import Production
from mael_cli.self.setup import Setup, SetupError
from mael_domain.dev_cert import DevCertError, cert_paths


@dataclass
class SetupRun:
    """``Setup`` on a tmp home, with fakes for every outside effect."""

    home: Path
    interactive: bool = False
    answer: str = ""
    asked: list[str] = field(default_factory=list)
    output: list[str] = field(default_factory=list)
    supervisor: FakeSupervisor = field(default_factory=FakeSupervisor)
    #: What the fake ``tailscale cert`` writes, or the error it raises.
    cert: bytes | DevCertError = b"cert"
    issued: list[str] = field(default_factory=list)

    @property
    def config(self) -> Path:
        return self.home / ".maelstrom" / "config.yaml"

    def __call__(self) -> None:
        def prompt(text: str, default: str) -> str:
            self.asked.append(text)
            return self.answer or default

        Setup(
            prod=Production(self.home),
            supervisor=self.supervisor,
            run=lambda argv: subprocess.CompletedProcess(argv, 1, "", ""),
            echo=self.output.append,
            inherited_path="/usr/bin",
            stop_main_services=lambda echo: None,
            integrate=list,
            install_plugins=lambda: [],
            interactive=self.interactive,
            prompt=prompt,
            issue_cert=self.issue_cert,
        )()

    def issue_cert(self, host: str) -> None:
        self.issued.append(host)
        if isinstance(self.cert, DevCertError):
            raise self.cert
        cert, key = cert_paths(host)
        cert.parent.mkdir(parents=True, exist_ok=True)
        cert.write_bytes(self.cert)
        key.write_bytes(b"key")

    @property
    def orchestrator(self):
        return next(s for s in self.supervisor.applied if s.name == ORCHESTRATOR)


@pytest.fixture
def bare_home(tmp_path, monkeypatch):
    """A home with no ``~/.maelstrom``, as a fresh install finds it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def test_setup_asks_for_the_projects_dir(bare_home):
    run = SetupRun(bare_home, interactive=True, answer=str(bare_home / "code"))

    run()

    assert len(run.asked) == 1
    assert f"projects_dir: {bare_home / 'code'}" in run.config.read_text()
    assert (bare_home / "code").is_dir()


def test_setup_without_a_tty_writes_the_default_projects_dir(bare_home):
    run = SetupRun(bare_home)

    run()

    assert run.asked == []
    assert "projects_dir: ~/Projects" in run.config.read_text()
    assert (bare_home / "Projects").is_dir()


def test_setup_keeps_a_set_projects_dir(bare_home):
    run = SetupRun(bare_home, interactive=True)
    run.config.parent.mkdir()
    run.config.write_text(f"projects_dir: {bare_home / 'src'}\n")

    run()

    assert run.asked == []
    assert run.config.read_text().startswith(f"projects_dir: {bare_home / 'src'}\n")


def test_setup_moves_the_production_keys_to_the_global_config(bare_home):
    run = SetupRun(bare_home)
    state = bare_home / ".maelstrom" / "state" / "config.yaml"
    state.parent.mkdir(parents=True)
    state.write_text("orchestrator_url: https://box:2772\nservice_path: /bin\n")

    run()
    moved = run.config.read_text()
    run()

    assert "service_path: /bin\n" in moved
    assert "orchestrator_url" not in moved
    assert not state.exists()
    assert run.config.read_text() == moved


def test_setup_names_what_is_left_in_the_old_notebook_config(bare_home):
    run = SetupRun(bare_home)
    state = bare_home / ".maelstrom" / "state" / "config.yaml"
    state.parent.mkdir(parents=True)
    state.write_text("orchestrator_url: https://box:2772\nopen_command: cursor\n")

    run()

    assert state.read_text() == "open_command: cursor\n"
    assert any(f"{state} is no longer read" in line for line in run.output)


def test_setup_keeps_the_global_value_when_both_files_set_a_key(bare_home):
    run = SetupRun(bare_home)
    run.config.parent.mkdir()
    run.config.write_text(f"projects_dir: {bare_home / 'src'}\nservice_path: /new\n")
    state = bare_home / ".maelstrom" / "state" / "config.yaml"
    state.parent.mkdir()
    state.write_text("service_path: /old\n")

    run()

    assert "service_path: /new\n" in run.config.read_text()
    assert "/old" not in run.config.read_text()
    assert not state.exists()


def test_setup_moves_projects_dir_up_before_it_asks(bare_home):
    run = SetupRun(bare_home, interactive=True)
    state = bare_home / ".maelstrom" / "state" / "config.yaml"
    state.parent.mkdir(parents=True)
    state.write_text(f"projects_dir: {bare_home / 'src'}\n")

    run()

    assert run.asked == []
    assert f"projects_dir: {bare_home / 'src'}\n" in run.config.read_text()


def test_setup_names_a_legacy_home_config(bare_home):
    legacy = bare_home / ".maelstrom.yaml"
    legacy.write_text("linear:\n  api_key: lin_x\n")
    run = SetupRun(bare_home)

    run()

    assert any(f"{legacy} is no longer read" in line for line in run.output)


def test_setup_quotes_an_answer_yaml_would_misread(bare_home):
    answer = str(bare_home / "my: projects")
    run = SetupRun(bare_home, interactive=True, answer=answer)

    run()

    assert yaml.safe_load(run.config.read_text())["projects_dir"] == answer
    assert Path(answer).is_dir()


def test_setup_refuses_a_relative_projects_dir(bare_home):
    run = SetupRun(bare_home, interactive=True, answer="code")

    with pytest.raises(SetupError, match="relative"):
        run()


def test_setup_quotes_a_bare_tilde_projects_dir(bare_home):
    """Plain ``~`` is YAML null, which reads as unset."""
    run = SetupRun(bare_home, interactive=True, answer="~")

    run()

    assert yaml.safe_load(run.config.read_text())["projects_dir"] == "~"


TLS_CONFIG = "projects_dir: ~/Projects\ndev_host: box.ts.net\ndev_https: true\n"


def test_setup_replaces_a_stored_url_with_the_one_dev_host_names(bare_home):
    """An older setup wrote ``http://localhost`` before ``dev_https:`` was on."""
    run = SetupRun(bare_home)
    run.config.parent.mkdir()
    run.config.write_text(TLS_CONFIG + "orchestrator_url: http://localhost:2772/\n")

    run()

    assert "orchestrator_url" not in run.config.read_text()
    assert any(
        "http://localhost:2772/" in line and "https://box.ts.net:2772" in line
        for line in run.output
    )
    argv = run.orchestrator.argv
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    assert argv[argv.index("--public-host") + 1] == "box.ts.net"
    cert, key = cert_paths("box.ts.net")
    assert run.orchestrator.env["DEV_TLS_CERT"] == str(cert)
    assert run.orchestrator.env["DEV_TLS_KEY"] == str(key)
    assert run.output[-1].endswith("runs at https://box.ts.net:2772.")


def test_setup_keeps_a_runners_parent_url(bare_home):
    run = SetupRun(bare_home)
    run.config.parent.mkdir()
    run.config.write_text(
        TLS_CONFIG + "role: runner\norchestrator_url: https://monolith.ts.net:2772\n"
    )

    run()

    assert "orchestrator_url: https://monolith.ts.net:2772" in run.config.read_text()


def test_setup_without_a_dev_host_serves_loopback_only(bare_home):
    run = SetupRun(bare_home)

    run()

    assert "--public-host" not in run.orchestrator.argv
    assert "DEV_TLS_CERT" not in run.orchestrator.env
    assert run.issued == []


@pytest.mark.parametrize(("old", "restarted"), [(b"old", True), (b"cert", False)])
def test_a_renewed_certificate_restarts_the_orchestrator(bare_home, old, restarted):
    """The server reads its certificate once, at start."""
    run = SetupRun(bare_home, supervisor=FakeSupervisor(start_on_apply=False))
    run.config.parent.mkdir()
    run.config.write_text(TLS_CONFIG)
    cert, _ = cert_paths("box.ts.net")
    cert.parent.mkdir(parents=True)
    cert.write_bytes(old)

    run()

    assert run.issued == ["box.ts.net"]
    assert (f"restart {ORCHESTRATOR}" in run.supervisor.calls) is restarted


def test_a_certificate_that_does_not_issue_is_named_and_setup_finishes(bare_home):
    run = SetupRun(bare_home, cert=DevCertError("tailscale cert box.ts.net failed"))
    run.config.parent.mkdir()
    run.config.write_text(TLS_CONFIG)

    run()

    assert any("tailscale cert box.ts.net failed" in line for line in run.output)
    assert run.output[-1].startswith("Setup complete.")
