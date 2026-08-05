"""Tests for GitHub polling helpers."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from maelstrom.github import CheckRun, PRInfo, create_project_repo, wait_for_merge


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


class TestCreateProjectRepo:
    """`create_project_repo` builds a seed commit, then creates + pushes the repo."""

    @staticmethod
    def _fake_run_cmd(url="git@github.com:me/proj.git", seen=None):
        """Stand in for run_cmd: record every call, answer `remote get-url`."""
        def _run(cmd, cwd=None, quiet=False, check=True, **kwargs):
            if seen is not None:
                seen.append((cmd, cwd))
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{url}\n", stderr="")
        return _run

    def _run_create(self, *args, url="git@github.com:me/proj.git", **kwargs):
        """Call create_project_repo with run_cmd mocked; return (result, calls)."""
        seen = []
        with patch("maelstrom.github.run_cmd", side_effect=self._fake_run_cmd(url, seen)):
            result = create_project_repo(*args, **kwargs)
        return result, seen

    def test_seeds_a_commit_then_creates_the_repo(self):
        _, seen = self._run_create("proj")
        cmds = [cmd for cmd, _ in seen]
        assert cmds[0][:2] == ["git", "init"]
        assert cmds[1] == ["git", "add", "-A"]
        assert cmds[2][:2] == ["git", "commit"]
        assert cmds[3][:3] == ["gh", "repo", "create"]

    def test_initial_branch_is_main(self):
        _, seen = self._run_create("proj")
        assert seen[0][0] == ["git", "init", "-b", "main"]

    def test_stub_files_exist_when_the_commit_runs(self):
        written = {}

        def _run(cmd, cwd=None, quiet=False, check=True, **kwargs):
            if cmd[:2] == ["git", "add"]:
                assert cwd is not None
                written.update({p.name: p.read_text() for p in cwd.iterdir()})
            return subprocess.CompletedProcess(cmd, 0, stdout="url\n", stderr="")

        with patch("maelstrom.github.run_cmd", side_effect=_run):
            create_project_repo("proj")

        assert set(written) == {".gitignore", ".maelstrom.yaml", "README.md", "CLAUDE.md"}
        assert "# proj" in written["README.md"]

    def test_private_by_default(self):
        _, seen = self._run_create("proj")
        gh_cmd = [cmd for cmd, _ in seen if cmd[0] == "gh"][0]
        assert "--private" in gh_cmd
        assert "--public" not in gh_cmd

    def test_public_flag(self):
        _, seen = self._run_create("proj", private=False)
        gh_cmd = [cmd for cmd, _ in seen if cmd[0] == "gh"][0]
        assert "--public" in gh_cmd
        assert "--private" not in gh_cmd

    def test_description_is_passed_through(self):
        _, seen = self._run_create("proj", description="A thing")
        gh_cmd = [cmd for cmd, _ in seen if cmd[0] == "gh"][0]
        assert gh_cmd[gh_cmd.index("--description") + 1] == "A thing"

    def test_description_omitted_when_absent(self):
        _, seen = self._run_create("proj")
        gh_cmd = [cmd for cmd, _ in seen if cmd[0] == "gh"][0]
        assert "--description" not in gh_cmd

    def test_owner_qualified_name_passes_to_gh_verbatim(self):
        _, seen = self._run_create("acme/proj")
        gh_cmd = [cmd for cmd, _ in seen if cmd[0] == "gh"][0]
        assert gh_cmd[3] == "acme/proj"
        # The local seed directory uses the bare name.
        assert seen[0][1] is not None and seen[0][1].name == "proj"

    def test_returns_the_stripped_clone_url(self):
        url, _ = self._run_create("proj", url="https://github.com/me/proj.git")
        assert url == "https://github.com/me/proj.git"

    def test_called_process_error_becomes_runtime_error(self):
        err = subprocess.CalledProcessError(1, ["gh"], stderr="name already exists")
        with patch("maelstrom.github.run_cmd", side_effect=err):
            with pytest.raises(RuntimeError, match="Failed to create GitHub repository"):
                create_project_repo("proj")

    def test_missing_gh_becomes_runtime_error(self):
        with patch("maelstrom.github.run_cmd", side_effect=FileNotFoundError()):
            with pytest.raises(RuntimeError, match="gh.*not installed"):
                create_project_repo("proj")
