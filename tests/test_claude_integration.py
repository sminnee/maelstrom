"""Installing maelstrom's own entries into `~/.claude/settings.json`.

The file belongs to Claude Code, so every writer here merges into what it
finds and leaves the rest alone.
"""

import json
from pathlib import Path

from maelstrom.claude_integration import (
    SANDBOX_EXCLUSIONS,
    install_sandbox_exclusions,
    remove_session_channel,
    remove_session_hooks,
)


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
