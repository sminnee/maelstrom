"""Tests for the launchd glue: plist rendering + opt-in gating.

The ``launchctl``/filesystem side effects are mocked; only :func:`render_plist`
is pure. ``HOME`` is redirected to a tmp dir so marker/plist/log paths land in a
sandbox.
"""

import subprocess
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

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

    def test_no_cmux_socket_and_no_password(self):
        xml = sl.render_plist(
            "/abs/bin/mael", agent_path="/abs/bin", log="/log/sched.log"
        )
        # The CLI defaults the socket path when unset, so the plist sets neither
        # the socket path nor any secret (launchd→cmux uses keychain auth).
        assert "CMUX_SOCKET_PATH" not in xml
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


@pytest.fixture
def no_power_commands(monkeypatch):
    """Fail the test if anything shells out to ``sudo`` or ``pmset``.

    The wake is launchd's job now (the hourly ``StartCalendarInterval`` fires on
    the next wake), so no code path may reach for the OS power scheduler.
    """

    def fake_run(args, **kwargs):
        if args and args[0] in ("sudo", "pmset"):
            raise AssertionError(f"unexpected power-scheduler call: {args}")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(sl.subprocess, "run", fake_run)


class TestEnsureScheduleAgent:
    def test_noop_when_marker_absent(self, home, darwin, launchctl, no_power_commands):
        msgs = sl.ensure_schedule_agent()
        assert not sl.plist_path().exists()
        launchctl.bootstrap.assert_not_called()
        assert any("not enabled" in m or "removed" in m for m in msgs)

    def test_installs_when_marker_present(
        self, home, darwin, launchctl, no_power_commands, monkeypatch
    ):
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


# --- the marker is a bare presence flag ---


class TestMarker:
    def test_install_marker_writes_empty_marker(self, home):
        sl.install_marker()
        assert sl.marker_path().read_text() == ""

    def test_install_rejects_wake_at_option(self, home, darwin, launchctl):
        """``--wake-at`` is gone: Click rejects it as an unknown option."""
        result = CliRunner().invoke(
            sl.schedule_group, ["install", "--wake-at", "09:00"]
        )
        assert result.exit_code == 2
        assert "no such option" in result.output.lower()


# --- uninstall clears a wake left by an older --wake-at install ---


class TestLeftoverWakeCleanup:
    """``mael schedule uninstall`` clears the repeating wake it used to set.

    Only the CLI command does this. ``ensure_schedule_agent`` runs unattended
    from ``mael install`` / ``mael self-update``, so a sudo prompt there would
    block those; a human is always present for ``uninstall``.
    """

    @pytest.fixture
    def pmset(self, monkeypatch):
        """Record pmset reads/cancels; ``state['repeating']`` sets what is set."""
        state = {"repeating": "", "cancelled": False}

        def fake_run(args, **kwargs):
            if args[:1] == ["pmset"]:
                return subprocess.CompletedProcess(args, 0, state["repeating"], "")
            if args[:4] == ["sudo", "pmset", "repeat", "cancel"]:
                state["cancelled"] = True
                return subprocess.CompletedProcess(args, 0, "", "")
            raise AssertionError(f"unexpected subprocess call: {args}")

        monkeypatch.setattr(sl.subprocess, "run", fake_run)
        return state

    def test_clears_a_leftover_wake(self, home, darwin, launchctl, pmset):
        pmset["repeating"] = (
            "Repeating power events:\n  wakepoweron at 7:59AM every day\n"
        )
        msgs = sl.clear_leftover_wake()
        assert pmset["cancelled"]
        assert any("cleared" in m for m in msgs)

    def test_no_sudo_when_no_wake_is_set(self, home, darwin, launchctl, pmset):
        """The common case: nothing set, so no sudo prompt."""
        msgs = sl.clear_leftover_wake()
        assert not pmset["cancelled"]
        assert msgs == []

    def test_ignores_non_repeating_events(self, home, darwin, launchctl, pmset):
        """One-off system alarms are not ours — they must not trigger a cancel."""
        pmset["repeating"] = (
            "Scheduled power events:\n"
            " [0]  wake at 08/11/2026 00:31:06 by 'com.apple.alarm.foo'\n"
        )
        sl.clear_leftover_wake()
        assert not pmset["cancelled"]


# --- _bootstrap tolerates the already-loaded race ---


class TestBootstrapRace:
    def test_already_bootstrapped_is_success(self, monkeypatch):
        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(
                args, 5, "", "Bootstrap failed: service already bootstrapped"
            )

        monkeypatch.setattr(sl.subprocess, "run", fake_run)
        # Should not raise.
        sl._bootstrap(sl.Path("/tmp/x.plist"))

    def test_genuine_failure_raises(self, monkeypatch):
        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, "", "Operation not permitted")

        monkeypatch.setattr(sl.subprocess, "run", fake_run)
        with pytest.raises(subprocess.CalledProcessError):
            sl._bootstrap(sl.Path("/tmp/x.plist"))

    def test_success_returns_cleanly(self, monkeypatch):
        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(sl.subprocess, "run", fake_run)
        sl._bootstrap(sl.Path("/tmp/x.plist"))


# --- status reporting ---


@pytest.fixture
def status_subprocess(monkeypatch):
    """Mock the read-only subprocess call status_lines makes.

    Returns a controller letting each test set whether the job is loaded. Any
    other command is a test failure — status must not shell out to ``pmset``.
    """

    state = {"loaded": False}

    def fake_run(args, **kwargs):
        if args[:2] == ["launchctl", "print"]:
            return subprocess.CompletedProcess(
                args, 0 if state["loaded"] else 1, "", ""
            )
        raise AssertionError(f"unexpected subprocess call: {args}")

    monkeypatch.setattr(sl.subprocess, "run", fake_run)
    return state


class TestStatus:
    def test_no_marker(self, home, darwin, status_subprocess):
        out = "\n".join(sl.status_lines())
        assert "Marker: absent" in out
        assert "Plist: absent" in out
        assert "Job loaded: no" in out

    def test_marker_not_loaded(self, home, darwin, status_subprocess):
        sl.install_marker()
        out = "\n".join(sl.status_lines())
        assert "Marker: present" in out
        assert "Job loaded: no" in out

    def test_loaded(self, home, darwin, status_subprocess):
        sl.install_marker()
        status_subprocess["loaded"] = True
        out = "\n".join(sl.status_lines())
        assert "Job loaded: yes" in out

    def test_log_tail(self, home, darwin, status_subprocess):
        sl.install_marker()
        sl.log_path().parent.mkdir(parents=True, exist_ok=True)
        sl.log_path().write_text("line one\nline two\n")
        out = "\n".join(sl.status_lines())
        assert "line two" in out

    def test_non_mac(self, home, monkeypatch):
        monkeypatch.setattr(sl.platform, "system", lambda: "Linux")
        out = "\n".join(sl.status_lines())
        assert "not macOS" in out
