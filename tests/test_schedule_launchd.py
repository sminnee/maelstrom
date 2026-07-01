"""Tests for the launchd glue: plist rendering + opt-in gating.

The ``launchctl``/filesystem side effects are mocked; only :func:`render_plist`
is pure. ``HOME`` is redirected to a tmp dir so marker/plist/log paths land in a
sandbox.
"""

import subprocess
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
    """Mock the launchctl bootstrap/bootout and pmset wake calls."""
    bootstrap = MagicMock()
    bootout = MagicMock()
    schedule_wake = MagicMock()
    clear_wake = MagicMock()
    monkeypatch.setattr(sl, "_bootstrap", bootstrap)
    monkeypatch.setattr(sl, "_bootout", bootout)
    monkeypatch.setattr(sl, "_schedule_wake", schedule_wake)
    monkeypatch.setattr(sl, "_clear_wake", clear_wake)
    return type(
        "LC",
        (),
        {
            "bootstrap": bootstrap,
            "bootout": bootout,
            "schedule_wake": schedule_wake,
            "clear_wake": clear_wake,
        },
    )()


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


# --- wake-time marker parsing ---


class TestWakeTime:
    def test_empty_marker_is_no_wake(self, home):
        sl.install_marker()
        assert sl.wake_time() is None

    def test_marker_carries_wake_time(self, home):
        sl.install_marker("09:00")
        assert sl.marker_path().read_text() == "09:00"
        assert sl.wake_time() == "09:00"

    def test_wake_time_normalises_single_digit_hour(self, home):
        sl.install_marker("9:05")
        assert sl.wake_time() == "09:05"

    def test_absent_marker_is_no_wake(self, home):
        assert sl.wake_time() is None

    def test_corrupt_marker_body_is_no_wake(self, home):
        sl.install_marker()
        sl.marker_path().write_text("not-a-time")
        assert sl.wake_time() is None

    @pytest.mark.parametrize("bad", ["24:00", "12:60", "99:99", "9", "9:5", "ab:cd"])
    def test_install_marker_rejects_bad_wake(self, home, bad):
        with pytest.raises(ValueError):
            sl.install_marker(bad)

    def test_minute_before_wraps_midnight(self):
        assert sl._minute_before("00:00") == "23:59"
        assert sl._minute_before("09:00") == "08:59"
        assert sl._minute_before("09:30") == "09:29"


# --- wake reconciliation in ensure_schedule_agent ---


class TestWakeReconciliation:
    def test_marker_with_wake_schedules_pmset(
        self, home, darwin, launchctl, monkeypatch
    ):
        monkeypatch.setattr(sl, "_mael_path", lambda: "/abs/bin/mael")
        # Force a mismatch so the real pmset read isn't hit and the set path fires.
        monkeypatch.setattr(sl, "_pmset_wake_hhmm", lambda: None)
        sl.install_marker("09:00")
        msgs = sl.ensure_schedule_agent()
        # Wake scheduled one minute before the intended fire.
        launchctl.schedule_wake.assert_called_once_with("09:00")
        launchctl.clear_wake.assert_not_called()
        assert any("08:59" in m for m in msgs)

    def test_marker_without_wake_clears_pmset(
        self, home, darwin, launchctl, monkeypatch
    ):
        monkeypatch.setattr(sl, "_mael_path", lambda: "/abs/bin/mael")
        sl.install_marker()
        msgs = sl.ensure_schedule_agent()
        launchctl.schedule_wake.assert_not_called()
        launchctl.clear_wake.assert_called_once()
        assert any("none configured" in m for m in msgs)

    def test_marker_absent_clears_pmset(self, home, darwin, launchctl):
        sl.ensure_schedule_agent()
        launchctl.clear_wake.assert_called_once()
        launchctl.schedule_wake.assert_not_called()

    def test_wake_failure_is_reported_not_raised(
        self, home, darwin, launchctl, monkeypatch
    ):
        monkeypatch.setattr(sl, "_mael_path", lambda: "/abs/bin/mael")
        monkeypatch.setattr(sl, "_pmset_wake_hhmm", lambda: None)
        launchctl.schedule_wake.side_effect = subprocess.CalledProcessError(
            1, ["sudo", "pmset"]
        )
        sl.install_marker("09:00")
        # The agent still loads; the wake failure is a message, not an exception.
        msgs = sl.ensure_schedule_agent()
        assert any("loaded" in m for m in msgs)
        assert any("pmset failed" in m for m in msgs)

    def test_skips_pmset_when_already_set(
        self, home, darwin, launchctl, monkeypatch
    ):
        """The core fix: skip the sudo pmset call when the wake already matches."""
        monkeypatch.setattr(sl, "_mael_path", lambda: "/abs/bin/mael")
        # Current repeating wake already equals _minute_before("09:00").
        monkeypatch.setattr(sl, "_pmset_wake_hhmm", lambda: "08:59")
        sl.install_marker("09:00")
        msgs = sl.ensure_schedule_agent()
        launchctl.schedule_wake.assert_not_called()
        assert any("unchanged" in m for m in msgs)

    def test_reapplies_pmset_when_time_differs(
        self, home, darwin, launchctl, monkeypatch
    ):
        monkeypatch.setattr(sl, "_mael_path", lambda: "/abs/bin/mael")
        monkeypatch.setattr(sl, "_pmset_wake_hhmm", lambda: "07:59")
        sl.install_marker("09:00")
        sl.ensure_schedule_agent()
        launchctl.schedule_wake.assert_called_once_with("09:00")

    def test_reapplies_pmset_when_none_set(
        self, home, darwin, launchctl, monkeypatch
    ):
        monkeypatch.setattr(sl, "_mael_path", lambda: "/abs/bin/mael")
        monkeypatch.setattr(sl, "_pmset_wake_hhmm", lambda: None)
        sl.install_marker("09:00")
        sl.ensure_schedule_agent()
        launchctl.schedule_wake.assert_called_once_with("09:00")

    def test_skips_pmset_via_real_parse(self, home, darwin, launchctl, monkeypatch):
        """End-to-end: a realistic pmset line matching the target skips the sudo call.

        Stubs _pmset_wake_line (not _pmset_wake_hhmm) so the real parse-and-compare
        path is exercised: '8:59AM' must normalise to '08:59' == _minute_before('09:00').
        """
        monkeypatch.setattr(sl, "_mael_path", lambda: "/abs/bin/mael")
        monkeypatch.setattr(
            sl, "_pmset_wake_line", lambda: "wakepoweron at 8:59AM every day"
        )
        sl.install_marker("09:00")
        msgs = sl.ensure_schedule_agent()
        launchctl.schedule_wake.assert_not_called()
        assert any("unchanged" in m for m in msgs)


class TestPmsetWakeHhmm:
    @pytest.mark.parametrize(
        "line,expected",
        [
            ("wakepoweron at 7:59AM every day", "07:59"),
            ("wakepoweron at 12:00AM every day", "00:00"),
            ("wakepoweron at 12:30PM every day", "12:30"),
            ("wakepoweron at 1:05PM every day", "13:05"),
            (None, None),
            ("some unparseable line", None),
        ],
    )
    def test_parses_repeating_wake_time(self, monkeypatch, line, expected):
        monkeypatch.setattr(sl, "_pmset_wake_line", lambda: line)
        assert sl._pmset_wake_hhmm() == expected


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
    """Mock the read-only subprocess calls status_lines makes.

    Returns a controller letting each test set whether the job is loaded and
    what ``pmset -g sched`` emits.
    """

    state = {"loaded": False, "pmset": ""}

    def fake_run(args, **kwargs):
        if args[:2] == ["launchctl", "print"]:
            return subprocess.CompletedProcess(
                args, 0 if state["loaded"] else 1, "", ""
            )
        if args[:1] == ["pmset"]:
            return subprocess.CompletedProcess(args, 0, state["pmset"], "")
        raise AssertionError(f"unexpected subprocess call: {args}")

    monkeypatch.setattr(sl.subprocess, "run", fake_run)
    return state


class TestStatus:
    def test_no_marker(self, home, darwin, status_subprocess):
        out = "\n".join(sl.status_lines())
        assert "Marker: absent" in out
        assert "Plist: absent" in out
        assert "Job loaded: no" in out
        assert "pmset wake: none" in out

    def test_marker_not_loaded(self, home, darwin, status_subprocess):
        sl.install_marker()
        out = "\n".join(sl.status_lines())
        assert "Marker: present" in out
        assert "no wake configured" in out
        assert "Job loaded: no" in out

    def test_loaded(self, home, darwin, status_subprocess):
        sl.install_marker()
        status_subprocess["loaded"] = True
        out = "\n".join(sl.status_lines())
        assert "Job loaded: yes" in out

    def test_loaded_with_wake(self, home, darwin, status_subprocess):
        sl.install_marker("09:00")
        status_subprocess["loaded"] = True
        status_subprocess["pmset"] = (
            "Repeating power events:\n"
            "  wakeorpoweron at 8:59AM every day\n"
        )
        out = "\n".join(sl.status_lines())
        assert "wake at 09:00" in out
        assert "Job loaded: yes" in out
        assert "wakeorpoweron" in out

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
