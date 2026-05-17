"""Tests for maelstrom.describe_change."""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import click
import pytest

from maelstrom.describe_change import (
    check_clean_worktree,
    check_range_non_empty,
    describe_change,
    resolve_range,
)


def setup_git_repo(repo_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo_path, check=True, capture_output=True,
    )


def create_commit(repo_path: Path, filename: str, content: str, message: str) -> str:
    (repo_path / filename).write_text(content)
    subprocess.run(["git", "add", filename], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo_path, check=True, capture_output=True,
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path, check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def setup_origin_main(repo_path: Path) -> None:
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=repo_path, check=True, capture_output=True,
    )


class TestResolveRange:
    def test_empty_returns_default(self):
        assert resolve_range(None) == "origin/main..HEAD"
        assert resolve_range("") == "origin/main..HEAD"

    def test_bare_sha_expands_to_single_commit_range(self):
        assert resolve_range("abc1234") == "abc1234^..abc1234"
        assert resolve_range("a" * 40) == "a" * 40 + "^.." + "a" * 40

    def test_range_passes_through(self):
        assert resolve_range("HEAD~3..HEAD") == "HEAD~3..HEAD"
        assert resolve_range("origin/main...HEAD") == "origin/main...HEAD"

    def test_non_sha_string_passes_through(self):
        # Not all-hex, so not treated as a SHA
        assert resolve_range("zzzzzzz") == "zzzzzzz"


class TestCheckCleanWorktree:
    def test_passes_when_clean(self):
        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            setup_git_repo(repo)
            create_commit(repo, "a.txt", "x", "init")
            check_clean_worktree(repo)  # no raise

    def test_raises_when_dirty(self):
        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            setup_git_repo(repo)
            create_commit(repo, "a.txt", "x", "init")
            (repo / "a.txt").write_text("dirty")
            with pytest.raises(click.ClickException, match="Commit your work"):
                check_clean_worktree(repo)


class TestCheckRangeNonEmpty:
    def test_passes_when_commits_in_range(self):
        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            setup_git_repo(repo)
            create_commit(repo, "a.txt", "x", "init")
            subprocess.run(
                ["git", "branch", "-M", "main"],
                cwd=repo, check=True, capture_output=True,
            )
            setup_origin_main(repo)
            create_commit(repo, "b.txt", "y", "second")
            check_range_non_empty(repo, "origin/main..HEAD")  # no raise

    def test_raises_when_no_commits(self):
        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            setup_git_repo(repo)
            create_commit(repo, "a.txt", "x", "init")
            subprocess.run(
                ["git", "branch", "-M", "main"],
                cwd=repo, check=True, capture_output=True,
            )
            setup_origin_main(repo)
            with pytest.raises(click.ClickException, match="No commits to review"):
                check_range_non_empty(repo, "origin/main..HEAD")

    def test_raises_on_invalid_range(self):
        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            setup_git_repo(repo)
            create_commit(repo, "a.txt", "x", "init")
            with pytest.raises(click.ClickException, match="Invalid range"):
                check_range_non_empty(repo, "nonexistent-ref..HEAD")


class TestDescribeChange:
    def test_returns_log_and_diff(self):
        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            setup_git_repo(repo)
            create_commit(repo, "a.txt", "x", "init")
            subprocess.run(
                ["git", "branch", "-M", "main"],
                cwd=repo, check=True, capture_output=True,
            )
            setup_origin_main(repo)
            create_commit(repo, "b.txt", "y\n", "feat: add b")

            result = describe_change(repo, "origin/main..HEAD")

            assert result.range == "origin/main..HEAD"
            assert "feat: add b" in result.log
            assert "b.txt" in result.diff
            assert "+y" in result.diff

    def test_render_has_expected_sections(self):
        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            setup_git_repo(repo)
            create_commit(repo, "a.txt", "x", "init")
            subprocess.run(
                ["git", "branch", "-M", "main"],
                cwd=repo, check=True, capture_output=True,
            )
            setup_origin_main(repo)
            create_commit(repo, "b.txt", "y\n", "feat: add b")

            rendered = describe_change(repo, "origin/main..HEAD").render()

            assert rendered.startswith("Range: origin/main..HEAD")
            assert "## Log" in rendered
            assert "## Diff" in rendered
