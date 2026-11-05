"""``Setup`` on a tmp home: the global config, and the keys an older setup left.

Every outside effect is a fake. ``test_self_cli.py`` runs the command itself.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml
from self_cli_support import FakeSupervisor

from mael_cli.self.production import Production
from mael_cli.self.setup import Setup, SetupError


@dataclass
class SetupRun:
    """``Setup`` on a tmp home, with fakes for every outside effect."""

    home: Path
    interactive: bool = False
    answer: str = ""
    asked: list[str] = field(default_factory=list)
    output: list[str] = field(default_factory=list)

    @property
    def config(self) -> Path:
        return self.home / ".maelstrom" / "config.yaml"

    def __call__(self) -> None:
        def prompt(text: str, default: str) -> str:
            self.asked.append(text)
            return self.answer or default

        Setup(
            prod=Production(self.home),
            supervisor=FakeSupervisor(),
            run=lambda argv: subprocess.CompletedProcess(argv, 1, "", ""),
            echo=self.output.append,
            inherited_path="/usr/bin",
            stop_main_services=lambda echo: None,
            integrate=list,
            interactive=self.interactive,
            prompt=prompt,
        )()


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

    assert "orchestrator_url: https://box:2772\n" in moved
    assert "service_path: /bin\n" in moved
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
    run.config.write_text(
        f"projects_dir: {bare_home / 'src'}\norchestrator_url: https://new:2772\n"
    )
    state = bare_home / ".maelstrom" / "state" / "config.yaml"
    state.parent.mkdir()
    state.write_text("orchestrator_url: https://old:2772\n")

    run()

    assert "orchestrator_url: https://new:2772\n" in run.config.read_text()
    assert "https://old" not in run.config.read_text()
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
