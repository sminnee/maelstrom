"""The wake an older ``--wake-at`` install left: found and cleared.

``pmset`` and ``sudo`` are fakes.
"""

import subprocess

import pytest

from mael_cli.self import schedule_launchd as sl


@pytest.fixture
def darwin(monkeypatch):
    monkeypatch.setattr(sl.platform, "system", lambda: "Darwin")


class TestLeftoverWakeCleanup:
    """``mael self uninstall`` clears the repeating wake an old install set.

    Only uninstall does this. Setup runs unattended, so a sudo prompt there
    would block it; a human is always present for ``uninstall``.
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

    def test_clears_a_leftover_wake(self, darwin, pmset):
        pmset["repeating"] = (
            "Repeating power events:\n  wakepoweron at 7:59AM every day\n"
        )
        msgs = sl.clear_leftover_wake()
        assert pmset["cancelled"]
        assert any("cleared" in m for m in msgs)

    def test_no_sudo_when_no_wake_is_set(self, darwin, pmset):
        """The common case: nothing set, so no sudo prompt."""
        msgs = sl.clear_leftover_wake()
        assert not pmset["cancelled"]
        assert msgs == []

    def test_ignores_non_repeating_events(self, darwin, pmset):
        """One-off system alarms are not ours — they must not trigger a cancel."""
        pmset["repeating"] = (
            "Scheduled power events:\n"
            " [0]  wake at 08/11/2026 00:31:06 by 'com.apple.alarm.foo'\n"
        )
        sl.clear_leftover_wake()
        assert not pmset["cancelled"]
