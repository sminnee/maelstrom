"""Tests for the launchd glue: plist rendering + opt-in gating.

The ``launchctl``/filesystem side effects are mocked; only :func:`render_plist`
is pure. ``HOME`` is redirected to a tmp dir so marker/plist/log paths land in a
sandbox.
"""

from unittest.mock import MagicMock

import pytest

from maelstrom import schedule_launchd as sl


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Redirect HOME so marker/plist/log paths live under a tmp dir."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Path.home() reads HOME on POSIX; ensure it picks up the override.
    monkeypatch.setattr(sl.Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


# --- render_plist (pure) ---


class TestRenderPlist:
    def test_contains_label_and_mael_path(self):
        xml = sl.render_plist(
            "/abs/bin/mael", agent_path="/abs/bin:/usr/bin", log="/log/sched.log"
        )
        assert f"<string>{sl.LABEL}</string>" in xml
        assert "<string>/abs/bin/mael</string>" in xml
        assert "add-scheduled" in xml
        assert "--all-projects" in xml
        assert "--run" in xml

    def test_sets_cmux_socket_no_password(self):
        xml = sl.render_plist(
            "/abs/bin/mael", agent_path="/abs/bin", log="/log/sched.log"
        )
        assert "CMUX_SOCKET_PATH" in xml
        assert sl.CMUX_SOCKET_PATH in xml
        # The crux: no secret in the plist (launchd→cmux uses keychain auth).
        assert "PASSWORD" not in xml
        assert "CMUX_SOCKET_PASSWORD" not in xml

    def test_run_at_load_and_hourly_interval(self):
        xml = sl.render_plist("/m", agent_path="/b", log="/l")
        assert "<key>RunAtLoad</key>" in xml
        assert "<true/>" in xml
        assert "<key>StartCalendarInterval</key>" in xml
        assert "<key>Minute</key>" in xml

    def test_log_paths(self):
        xml = sl.render_plist("/m", agent_path="/b", log="/var/sched.log")
        assert "<key>StandardOutPath</key>" in xml
        assert "<key>StandardErrorPath</key>" in xml
        assert xml.count("/var/sched.log") == 2


# --- ensure_schedule_agent: opt-in gating ---


@pytest.fixture
def darwin(monkeypatch):
    monkeypatch.setattr(sl.platform, "system", lambda: "Darwin")


@pytest.fixture
def launchctl(monkeypatch):
    """Mock the launchctl bootstrap/bootout calls."""
    bootstrap = MagicMock()
    bootout = MagicMock()
    monkeypatch.setattr(sl, "_bootstrap", bootstrap)
    monkeypatch.setattr(sl, "_bootout", bootout)
    return type("LC", (), {"bootstrap": bootstrap, "bootout": bootout})()


class TestEnsureScheduleAgent:
    def test_noop_when_marker_absent(self, home, darwin, launchctl):
        msgs = sl.ensure_schedule_agent()
        assert not sl.plist_path().exists()
        launchctl.bootstrap.assert_not_called()
        assert any("not enabled" in m or "removed" in m for m in msgs)

    def test_installs_when_marker_present(self, home, darwin, launchctl, monkeypatch):
        monkeypatch.setattr(sl, "_mael_path", lambda: "/abs/bin/mael")
        sl.install_marker()
        msgs = sl.ensure_schedule_agent()
        assert sl.plist_path().exists()
        assert "/abs/bin/mael" in sl.plist_path().read_text()
        launchctl.bootstrap.assert_called_once()
        assert any("loaded" in m for m in msgs)

    def test_uninstall_removes_stale_plist(self, home, darwin, launchctl):
        sl.install_marker()
        sl.ensure_schedule_agent()
        assert sl.plist_path().exists()
        # Now opt out: marker gone -> agent torn down.
        sl.uninstall_marker()
        msgs = sl.ensure_schedule_agent()
        assert not sl.plist_path().exists()
        launchctl.bootout.assert_called()
        assert any("removed" in m for m in msgs)

    def test_skipped_off_macos(self, home, monkeypatch, launchctl):
        monkeypatch.setattr(sl.platform, "system", lambda: "Linux")
        sl.install_marker()  # even with marker, non-mac is a no-op
        msgs = sl.ensure_schedule_agent()
        assert not sl.plist_path().exists()
        launchctl.bootstrap.assert_not_called()
        assert any("not macOS" in m for m in msgs)

    def test_self_heals_mael_path(self, home, darwin, launchctl, monkeypatch):
        sl.install_marker()
        monkeypatch.setattr(sl, "_mael_path", lambda: "/old/mael")
        sl.ensure_schedule_agent()
        assert "/old/mael" in sl.plist_path().read_text()
        # A later install/self-update with a new path rewrites the plist.
        monkeypatch.setattr(sl, "_mael_path", lambda: "/new/mael")
        sl.ensure_schedule_agent()
        assert "/new/mael" in sl.plist_path().read_text()


# --- install_claude_integration gating ---


def test_install_integration_skips_launchd_without_marker(home, monkeypatch):
    """install_claude_integration must not touch launchd without the marker."""
    from maelstrom import claude_integration as ci

    monkeypatch.setattr(sl.platform, "system", lambda: "Darwin")
    bootstrap = MagicMock()
    monkeypatch.setattr(sl, "_bootstrap", bootstrap)
    monkeypatch.setattr(sl, "_bootout", MagicMock())
    # Avoid the heavyweight skill/hook/channel work; just exercise the wire-in.
    monkeypatch.setattr(ci, "get_shared_dir", lambda: home / "nonexistent-shared")
    msgs = ci.install_claude_integration(monitor=False)
    bootstrap.assert_not_called()
    assert any("not enabled" in m for m in msgs)
