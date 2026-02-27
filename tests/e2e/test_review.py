"""E2E tests for review commands (squash, status)."""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from maelstrom.review import find_fixup_commits, squash_fixups

from .conftest import create_commit, run_git, setup_git_repo


def setup_origin_main(repo_path):
    """Create refs/remotes/origin/main pointing to HEAD."""
    run_git(repo_path, "update-ref", "refs/remotes/origin/main", "HEAD")


@pytest.mark.e2e
class TestSquashFixups:
    """Test fixup commit squashing with real git repos."""

    def test_squash_fixup_commits(self, tmp_path):
        """Scenario 32: squash merges fixup commits into their targets."""
        repo = tmp_path / "review-repo"
        repo.mkdir()
        setup_git_repo(repo)

        # Initial commit on main
        create_commit(repo, "README.md", "# Test", "Initial commit")
        run_git(repo, "branch", "-M", "main")
        setup_origin_main(repo)

        # Feature branch
        run_git(repo, "checkout", "-b", "feature/squash-test")

        # Normal commit + two fixups targeting it
        create_commit(repo, "feature.txt", "feature v1", "Add feature")
        create_commit(repo, "feature.txt", "feature v2", "fixup! Add feature")
        create_commit(repo, "feature.txt", "feature v3", "fixup! Add feature")

        # Squash
        result = squash_fixups(repo)
        assert result.success
        assert result.fixup_count == 2

        # Should have only 1 commit after merge-base (the squashed one)
        log_result = run_git(repo, "log", "--oneline", "refs/remotes/origin/main..HEAD")
        commits = [l for l in log_result.stdout.strip().splitlines() if l.strip()]
        assert len(commits) == 1
        assert "Add feature" in commits[0]

    def test_find_fixup_commits(self, tmp_path):
        """Scenario 33: find_fixup_commits returns fixup commit info."""
        repo = tmp_path / "fixup-repo"
        repo.mkdir()
        setup_git_repo(repo)

        create_commit(repo, "README.md", "# Test", "Initial commit")
        run_git(repo, "branch", "-M", "main")
        setup_origin_main(repo)

        run_git(repo, "checkout", "-b", "feature/find-fixups")
        create_commit(repo, "a.txt", "a", "Normal commit")
        create_commit(repo, "b.txt", "b", "fixup! Normal commit")

        fixups = find_fixup_commits(repo)
        assert len(fixups) == 1
        sha, subject = fixups[0]
        assert subject == "fixup! Normal commit"
        assert len(sha) > 0

    def test_squash_no_fixups(self, tmp_path):
        """Scenario 34: squash with no fixups is a no-op."""
        repo = tmp_path / "noop-repo"
        repo.mkdir()
        setup_git_repo(repo)

        create_commit(repo, "README.md", "# Test", "Initial commit")
        run_git(repo, "branch", "-M", "main")
        setup_origin_main(repo)

        run_git(repo, "checkout", "-b", "feature/no-fixups")
        create_commit(repo, "a.txt", "a", "Normal commit")

        result = squash_fixups(repo)
        assert result.success
        assert result.fixup_count == 0
