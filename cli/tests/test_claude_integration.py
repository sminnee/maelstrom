"""Installing maelstrom's own entries into `~/.claude/settings.json`.

The file belongs to Claude Code, so every writer here merges into what it
finds and leaves the rest alone.
"""

import json
from pathlib import Path

import pytest

from mael_cli.claude_integration import (
    SANDBOX_EXCLUSIONS,
    install_sandbox_exclusions,
    remove_session_channel,
    remove_session_hooks,
)
from mael_domain import shared_dir
from mael_domain.shared_dir import agent_prompt_file, investigation_prompt_file


def _settings(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data))
    return path


def _read(path: Path) -> list[str]:
    return json.loads(path.read_text())["sandbox"]["excludedCommands"]


class TestSandboxExclusions:
    """`mael task` reaches the daemon socket, the notebook and a worktree.

    A sandbox denies all three, so the launch verbs run outside it. Without
    this a session cannot start the next task in its own chain.
    """

    def test_it_adds_the_exclusions_to_a_file_that_has_none(self, tmp_path):
        path = _settings(tmp_path, {})
        install_sandbox_exclusions(path)
        assert _read(path) == list(SANDBOX_EXCLUSIONS)

    def test_it_keeps_the_exclusions_already_there(self, tmp_path):
        path = _settings(
            tmp_path, {"sandbox": {"excludedCommands": ["git push:*", "mael gh:*"]}}
        )
        install_sandbox_exclusions(path)
        assert _read(path)[:2] == ["git push:*", "mael gh:*"]
        assert set(SANDBOX_EXCLUSIONS) <= set(_read(path))

    def test_it_adds_nothing_twice(self, tmp_path):
        """`mael admin install` is run repeatedly, so it must not accumulate."""
        path = _settings(tmp_path, {})
        install_sandbox_exclusions(path)
        install_sandbox_exclusions(path)
        assert _read(path) == list(SANDBOX_EXCLUSIONS)

    def test_it_leaves_the_rest_of_the_file_alone(self, tmp_path):
        """The file is Claude Code's, not maelstrom's."""
        path = _settings(
            tmp_path,
            {"permissions": {"allow": ["Bash(mael:*)"]}, "sandbox": {"enabled": True}},
        )
        install_sandbox_exclusions(path)
        data = json.loads(path.read_text())
        assert data["permissions"] == {"allow": ["Bash(mael:*)"]}
        assert data["sandbox"]["enabled"] is True

    def test_it_refuses_a_non_object_sandbox_rather_than_overwriting(self, tmp_path):
        path = _settings(tmp_path, {"sandbox": "on"})
        messages = install_sandbox_exclusions(path)
        assert json.loads(path.read_text())["sandbox"] == "on"
        assert any("sandbox" in m for m in messages)


class TestRemoveSessionHooks:
    """An older `mael install` wrote hooks running `mael session record`.

    That command is gone with the session registry, so every one of them now
    runs a missing binary. `mael install` clears them on the next run.
    """

    def test_removes_a_mael_hook(self, tmp_path):
        path = _settings(
            tmp_path,
            {
                "hooks": {
                    "Stop": [
                        {
                            "matcher": "",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "mael session record stop",
                                }
                            ],
                        }
                    ]
                }
            },
        )
        messages = remove_session_hooks(path)
        assert messages
        assert json.loads(path.read_text())["hooks"] == {}

    def test_keeps_someone_elses_hook_in_the_same_block(self, tmp_path):
        path = _settings(
            tmp_path,
            {
                "hooks": {
                    "Stop": [
                        {
                            "matcher": "",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "mael session record stop",
                                },
                                {"type": "command", "command": "notify-send done"},
                            ],
                        }
                    ]
                }
            },
        )
        remove_session_hooks(path)
        blocks = json.loads(path.read_text())["hooks"]["Stop"]
        assert blocks == [
            {
                "matcher": "",
                "hooks": [{"type": "command", "command": "notify-send done"}],
            }
        ]

    def test_leaves_an_unrelated_event_alone(self, tmp_path):
        other = {
            "PreToolUse": [
                {
                    "matcher": "",
                    "hooks": [{"type": "command", "command": "some-linter"}],
                }
            ]
        }
        path = _settings(tmp_path, {"hooks": dict(other)})
        assert remove_session_hooks(path) == []
        assert json.loads(path.read_text())["hooks"] == other

    def test_a_file_without_hooks_is_a_noop(self, tmp_path):
        path = _settings(tmp_path, {"sandbox": {"excludedCommands": []}})
        assert remove_session_hooks(path) == []

    def test_a_missing_file_is_a_noop(self, tmp_path):
        assert remove_session_hooks(tmp_path / "nope.json") == []


class TestRemoveSessionChannel:
    """The MCP entry pointed at `mael session-channel`, which no longer exists."""

    def test_removes_the_entry(self, tmp_path):
        path = _settings(
            tmp_path,
            {"mcpServers": {"mael-session": {"command": "mael"}, "other": {}}},
        )
        assert remove_session_channel(path)
        assert json.loads(path.read_text())["mcpServers"] == {"other": {}}

    def test_absent_entry_is_a_noop(self, tmp_path):
        path = _settings(tmp_path, {"mcpServers": {"other": {}}})
        assert remove_session_channel(path) == []

    def test_leaves_a_repurposed_key_alone(self, tmp_path):
        # The key is the user's. One pointing at someone else's server is not
        # ours to delete on a name match.
        mine = {"mcpServers": {"mael-session": {"command": "my-own-server"}}}
        path = _settings(tmp_path, dict(mine))
        assert remove_session_channel(path) == []
        assert json.loads(path.read_text()) == mine

    def test_a_missing_file_is_a_noop(self, tmp_path):
        assert remove_session_channel(tmp_path / "nope.json") == []


def test_the_agent_prompt_file_teaches_the_markers_the_orchestrator_reads():
    """Only a driven agent has an orchestrator, so its launch names this file."""
    prompt = agent_prompt_file()
    assert prompt is not None
    assert prompt.read_text().startswith("You run under the maelstrom agent daemon.")


def test_no_shared_dir_means_no_agent_prompt_file(monkeypatch):
    """A missing prompt costs a note; a refused launch costs the session."""

    def gone():
        raise FileNotFoundError("shared")

    monkeypatch.setattr(shared_dir, "get_shared_dir", gone)
    assert agent_prompt_file() is None


@pytest.fixture
def shared(monkeypatch, tmp_path):
    """A shared dir of two known prompt files, and a home for the joined one."""
    root = tmp_path / "shared"
    root.mkdir()
    (root / "agent-prompt.md").write_text("markers\n")
    (root / "investigation-prompt.md").write_text("rules\n")
    monkeypatch.setattr(shared_dir, "get_shared_dir", lambda: root)
    monkeypatch.setattr(shared_dir, "get_maelstrom_dir", lambda: tmp_path / "home")
    return root


def test_the_investigation_prompt_file_joins_the_markers_and_the_rules(
    shared, tmp_path
):
    """The daemon takes one prompt file, so an investigation's carries both."""
    prompt = investigation_prompt_file()
    assert prompt == tmp_path / "home" / "agent-prompts" / "investigation.md"
    assert prompt.read_text() == "markers\n\nrules\n"


def test_an_unchanged_investigation_prompt_is_not_rewritten(shared):
    """A resumed agent names the file again; its content stays the same file."""
    first = investigation_prompt_file()
    assert first is not None
    stamp = first.stat().st_mtime_ns
    assert investigation_prompt_file() == first
    assert first.stat().st_mtime_ns == stamp


def test_changed_rules_reach_the_investigation_prompt(shared):
    """An upgrade that edits the rules must not leave agents on the old ones."""
    investigation_prompt_file()
    (shared / "investigation-prompt.md").write_text("new rules\n")
    prompt = investigation_prompt_file()
    assert prompt is not None
    assert prompt.read_text() == "markers\n\nnew rules\n"


def test_no_investigation_rules_means_no_investigation_prompt_file(shared):
    """Without its rules an investigation is an ordinary free agent: no file."""
    (shared / "investigation-prompt.md").unlink()
    assert investigation_prompt_file() is None


def test_the_shipped_investigation_rules_forbid_a_commit(monkeypatch, tmp_path):
    monkeypatch.setattr(shared_dir, "get_maelstrom_dir", lambda: tmp_path)
    prompt = investigation_prompt_file()
    assert prompt is not None
    assert "Do not commit" in prompt.read_text()
