"""Tests for GitHub polling helpers."""

from pathlib import Path
from unittest.mock import patch

import pytest

from maelstrom.github import CheckRun, PRInfo, wait_for_merge


def _pr(state="OPEN", merged=False, number=7):
    return PRInfo(
        number=number,
        title="A PR",
        url="https://example/pr",
        state=state,
        merged=merged,
        head_ref="feature",
    )


def _check(name, state):
    return CheckRun(name=name, state=state, run_id=None, link="")


class TestWaitForMerge:
    def test_returns_when_already_merged(self):
        pr = _pr(state="MERGED", merged=True)
        with patch("maelstrom.github.get_pr_info", return_value=pr), \
             patch("maelstrom.github.get_pr_checks", return_value=[]):
            result = wait_for_merge(Path("."), timeout=10, poll_interval=0)

        assert result is pr

    def test_merges_after_polling(self):
        infos = [_pr(state="OPEN"), _pr(state="OPEN"), _pr(state="MERGED", merged=True)]
        with patch("maelstrom.github.get_pr_info", side_effect=infos), \
             patch("maelstrom.github.get_pr_checks", return_value=[_check("ci", "PENDING")]), \
             patch("maelstrom.github.time.sleep"):
            result = wait_for_merge(Path("."), timeout=10, poll_interval=0)

        assert result.merged is True

    def test_closed_unmerged_raises(self):
        with patch("maelstrom.github.get_pr_info", return_value=_pr(state="CLOSED")), \
             patch("maelstrom.github.get_pr_checks", return_value=[]):
            with pytest.raises(RuntimeError, match="closed without merging"):
                wait_for_merge(Path("."), timeout=10, poll_interval=0)

    def test_terminal_failed_check_raises(self):
        with patch("maelstrom.github.get_pr_info", return_value=_pr(state="OPEN")), \
             patch("maelstrom.github.get_pr_checks",
                   return_value=[_check("lint", "SUCCESS"), _check("test", "FAILURE")]):
            with pytest.raises(RuntimeError, match="failing checks: test"):
                wait_for_merge(Path("."), timeout=10, poll_interval=0)

    def test_pending_checks_do_not_raise(self):
        """A pending (non-terminal) check keeps waiting rather than failing."""
        infos = [_pr(state="OPEN"), _pr(state="MERGED", merged=True)]
        with patch("maelstrom.github.get_pr_info", side_effect=infos), \
             patch("maelstrom.github.get_pr_checks",
                   return_value=[_check("test", "PENDING")]), \
             patch("maelstrom.github.time.sleep"):
            result = wait_for_merge(Path("."), timeout=10, poll_interval=0)

        assert result.merged is True

    def test_timeout_raises(self):
        with patch("maelstrom.github.get_pr_info", return_value=_pr(state="OPEN")), \
             patch("maelstrom.github.get_pr_checks", return_value=[]), \
             patch("maelstrom.github.time.sleep"):
            with pytest.raises(TimeoutError, match="to merge"):
                wait_for_merge(Path("."), timeout=0, poll_interval=0)
