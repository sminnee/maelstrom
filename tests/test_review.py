"""Tests for maelstrom.review module."""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from maelstrom.review import (
    find_fixup_commits,
    get_merge_base,
    squash_fixups,
    SquashResult,
)


def setup_git_repo(repo_path: Path) -> None:
    """Initialize a git repo with basic config."""
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )


def create_commit(repo_path: Path, filename: str, content: str, message: str) -> str:
    """Create a file and commit it, return the commit SHA."""
    (repo_path / filename).write_text(content)
    subprocess.run(["git", "add", filename], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def setup_origin_main(repo_path: Path) -> None:
    """Create refs/remotes/origin/main pointing to current HEAD."""
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )


class TestGetMergeBase:
    """Tests for get_merge_base function."""

    def test_finds_merge_base(self):
        """Test that merge-base is correctly found."""
        with TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            setup_git_repo(repo_path)

            # Initial commit
            create_commit(repo_path, "README.md", "# Test", "Initial commit")
            subprocess.run(
                ["git", "branch", "-M", "main"],
                cwd=repo_path,
                check=True,
                capture_output=True,
            )
            setup_origin_main(repo_path)

            # Get initial SHA
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                check=True,
                capture_output=True,
                text=True,
            )
            base_sha = result.stdout.strip()

            # Add more commits
            create_commit(repo_path, "feature.py", "def foo(): pass", "Add foo")

            # Verify merge-base is the initial commit
            merge_base = get_merge_base(repo_path)
            assert merge_base == base_sha

    def test_raises_on_no_origin_main(self):
        """Test that RuntimeError is raised when origin/main doesn't exist."""
        with TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            setup_git_repo(repo_path)
            create_commit(repo_path, "README.md", "# Test", "Initial commit")

            with pytest.raises(RuntimeError, match="Failed to find merge-base"):
                get_merge_base(repo_path)


class TestFindFixupCommits:
    """Tests for find_fixup_commits function."""

    def test_finds_fixup_commits(self):
        """Test that fixup commits are found."""
        with TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            setup_git_repo(repo_path)

            # Initial commit
            create_commit(repo_path, "README.md", "# Test", "Initial commit")
            subprocess.run(
                ["git", "branch", "-M", "main"],
                cwd=repo_path,
                check=True,
                capture_output=True,
            )
            setup_origin_main(repo_path)

            # Feature commit
            create_commit(repo_path, "feature.py", "def foo(): pass", "Add foo function")

            # Fixup commit
            create_commit(
                repo_path,
                "feature.py",
                "def foo():\n    return 1",
                "fixup! Add foo function",
            )

            fixups = find_fixup_commits(repo_path)

            assert len(fixups) == 1
            sha, subject = fixups[0]
            assert len(sha) == 40  # Full SHA
            assert subject == "fixup! Add foo function"

    def test_returns_empty_when_no_fixups(self):
        """Test that empty list is returned when no fixup commits."""
        with TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            setup_git_repo(repo_path)

            create_commit(repo_path, "README.md", "# Test", "Initial commit")
            subprocess.run(
                ["git", "branch", "-M", "main"],
                cwd=repo_path,
                check=True,
                capture_output=True,
            )
            setup_origin_main(repo_path)

            create_commit(repo_path, "feature.py", "def foo(): pass", "Add foo function")

            fixups = find_fixup_commits(repo_path)
            assert fixups == []

    def test_returns_empty_when_no_origin_main(self):
        """Test that empty list is returned when origin/main doesn't exist."""
        with TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            setup_git_repo(repo_path)
            create_commit(repo_path, "README.md", "# Test", "Initial commit")

            fixups = find_fixup_commits(repo_path)
            assert fixups == []

    def test_finds_multiple_fixups(self):
        """Test that multiple fixup commits are found."""
        with TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            setup_git_repo(repo_path)

            create_commit(repo_path, "README.md", "# Test", "Initial commit")
            subprocess.run(
                ["git", "branch", "-M", "main"],
                cwd=repo_path,
                check=True,
                capture_output=True,
            )
            setup_origin_main(repo_path)

            # Two feature commits
            create_commit(repo_path, "foo.py", "def foo(): pass", "Add foo")
            create_commit(repo_path, "bar.py", "def bar(): pass", "Add bar")

            # Fixups for both
            create_commit(repo_path, "foo.py", "def foo(): return 1", "fixup! Add foo")
            create_commit(repo_path, "bar.py", "def bar(): return 2", "fixup! Add bar")

            fixups = find_fixup_commits(repo_path)
            assert len(fixups) == 2
            subjects = [s for _, s in fixups]
            assert "fixup! Add foo" in subjects
            assert "fixup! Add bar" in subjects


class TestSquashFixups:
    """Tests for squash_fixups function."""

    def test_squashes_fixup_commits(self):
        """Test that fixup commits are squashed."""
        with TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            setup_git_repo(repo_path)

            create_commit(repo_path, "README.md", "# Test", "Initial commit")
            subprocess.run(
                ["git", "branch", "-M", "main"],
                cwd=repo_path,
                check=True,
                capture_output=True,
            )
            setup_origin_main(repo_path)

            # Feature commit
            create_commit(repo_path, "feature.py", "def foo(): pass", "Add foo function")

            # Fixup commit
            create_commit(
                repo_path,
                "feature.py",
                "def foo():\n    return 1",
                "fixup! Add foo function",
            )

            # Count commits before
            result = subprocess.run(
                ["git", "rev-list", "--count", "refs/remotes/origin/main..HEAD"],
                cwd=repo_path,
                check=True,
                capture_output=True,
                text=True,
            )
            commits_before = int(result.stdout.strip())
            assert commits_before == 2  # feature + fixup

            # Squash
            result = squash_fixups(repo_path)

            assert result.success is True
            assert result.fixup_count == 1
            assert result.commits_affected == 1

            # Count commits after
            proc = subprocess.run(
                ["git", "rev-list", "--count", "refs/remotes/origin/main..HEAD"],
                cwd=repo_path,
                check=True,
                capture_output=True,
                text=True,
            )
            commits_after = int(proc.stdout.strip())
            assert commits_after == 1  # fixup squashed into feature

    def test_returns_success_when_no_fixups(self):
        """Test that squash returns success when no fixup commits."""
        with TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            setup_git_repo(repo_path)

            create_commit(repo_path, "README.md", "# Test", "Initial commit")
            subprocess.run(
                ["git", "branch", "-M", "main"],
                cwd=repo_path,
                check=True,
                capture_output=True,
            )
            setup_origin_main(repo_path)

            create_commit(repo_path, "feature.py", "def foo(): pass", "Add foo")

            result = squash_fixups(repo_path)

            assert result.success is True
            assert result.fixup_count == 0
            assert "No fixup commits" in result.message

    def test_multiple_fixups_for_same_commit(self):
        """Test squashing multiple fixups for the same original commit."""
        with TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            setup_git_repo(repo_path)

            create_commit(repo_path, "README.md", "# Test", "Initial commit")
            subprocess.run(
                ["git", "branch", "-M", "main"],
                cwd=repo_path,
                check=True,
                capture_output=True,
            )
            setup_origin_main(repo_path)

            # Feature commit
            create_commit(repo_path, "feature.py", "def foo(): pass", "Add foo")

            # Multiple fixups
            create_commit(repo_path, "feature.py", "def foo():\n    x = 1", "fixup! Add foo")
            create_commit(
                repo_path, "feature.py", "def foo():\n    x = 1\n    return x", "fixup! Add foo"
            )

            result = squash_fixups(repo_path)

            assert result.success is True
            assert result.fixup_count == 2
            assert result.commits_affected == 1  # Both target same commit

            # Should be only 1 commit after squash
            proc = subprocess.run(
                ["git", "rev-list", "--count", "refs/remotes/origin/main..HEAD"],
                cwd=repo_path,
                check=True,
                capture_output=True,
                text=True,
            )
            assert int(proc.stdout.strip()) == 1


class TestSquashResult:
    """Tests for SquashResult dataclass."""

    def test_default_values(self):
        """Test that default values are correct."""
        result = SquashResult(success=True, message="test")
        assert result.fixup_count == 0
        assert result.commits_affected == 0

    def test_all_values(self):
        """Test with all values specified."""
        result = SquashResult(
            success=True,
            message="Squashed 3 commits",
            fixup_count=3,
            commits_affected=2,
        )
        assert result.success is True
        assert result.message == "Squashed 3 commits"
        assert result.fixup_count == 3
        assert result.commits_affected == 2
