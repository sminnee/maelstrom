"""What ``MAEL_STATE_ROOT`` must *not* move.

Only the state database splits per worktree. Everything else under
``~/.maelstrom`` stays shared, and each class below says why its own case is.

These pin *which resolver each module calls*, so a later move of the override
onto ``get_maelstrom_dir`` fails here rather than silently un-sharing.
"""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _a_pinned_home(monkeypatch, tmp_path):
    """Pin ``~`` so each expected path is a literal, not a second derivation.

    Asserting against ``get_maelstrom_dir()`` would make both sides move
    together: under the regression these tests exist to catch — the override
    moved onto ``get_maelstrom_dir`` — a recomputed expectation still matches,
    and every assertion here passes while the sharing is gone.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("MAEL_STATE_ROOT", str(tmp_path / "playpens" / "bravo"))


class TestPortAllocationStaysShared:
    """Cross-worktree by design: it exists so two worktrees never collide."""

    def test_the_allocations_file_is_on_the_shared_root(self, tmp_path):
        from maelstrom.ports import _get_allocations_path

        expected = tmp_path / ".maelstrom" / "port_allocations.json"
        assert _get_allocations_path() == expected


class TestEnvironmentStateStaysShared:
    """A playpen runs no services of its own; `mael env` is one view of the machine."""

    def test_the_log_dir_is_on_the_shared_root(self, tmp_path):
        from maelstrom.env import _get_log_dir

        expected = tmp_path / ".maelstrom" / "logs" / "proj" / "bravo"
        assert _get_log_dir("proj", "bravo") == expected

    def test_the_env_state_dir_is_on_the_shared_root(self, tmp_path):
        from maelstrom.env_store import get_state_dir

        assert get_state_dir() == tmp_path / ".maelstrom" / "envs"


class TestTheTaskExportStaysShared:
    """The export is out of scope: the wiki shares that root."""

    def test_the_tasks_root_is_on_the_shared_root(self, tmp_path):
        from maelstrom.task_store import tasks_root

        assert tasks_root() == tmp_path / ".maelstrom" / "tasks"


class TestConfigStaysShared:
    """So a playpen shares your API keys rather than needing its own."""

    def test_the_global_config_is_read_from_the_shared_root(self, tmp_path):
        import maelstrom.context as context

        (tmp_path / ".maelstrom").mkdir(parents=True)
        (tmp_path / ".maelstrom" / "config.yaml").write_text(
            "projects_dir: /from/the/shared/root\n"
        )
        assert context.load_global_config().projects_dir == Path(
            "/from/the/shared/root"
        )


class TestDaemonRootsStaySeparate:
    """The agent daemon has its own override; the two roots never fold together."""

    def test_daemon_roots_are_enumerated_from_the_shared_root(self, tmp_path):
        from maelstrom.agent_transport import all_roots

        daemons = tmp_path / ".maelstrom" / "daemons"
        (daemons / "bravo").mkdir(parents=True)
        assert daemons / "bravo" in [paths.root for paths in all_roots()]
