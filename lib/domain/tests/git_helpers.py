"""Shared git test helpers used across unit and e2e tests."""

import subprocess

from mael_domain.worktree_model import history_ref_prefix


def run_git(cwd, *args, check=True):
    """Run a git command and return CompletedProcess."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


def setup_git_repo(path):
    """Initialize a git repo with user config."""
    run_git(path, "init")
    run_git(path, "config", "user.email", "test@test.com")
    run_git(path, "config", "user.name", "Test")


def create_commit(path, filename, content, message):
    """Create a file, stage, and commit. Return SHA."""
    (path / filename).write_text(content)
    run_git(path, "add", filename)
    run_git(path, "commit", "-m", message)
    result = run_git(path, "rev-parse", "HEAD")
    return result.stdout.strip()


def setup_origin_main(repo_path):
    """Create refs/remotes/origin/main pointing to current HEAD."""
    run_git(repo_path, "update-ref", "refs/remotes/origin/main", "HEAD")


def three_commits(worktree_path):
    """Three ordinary commits on the current branch."""
    create_commit(worktree_path, "one.txt", "one\n", "feat: one")
    create_commit(worktree_path, "two.txt", "two\n", "feat: two")
    create_commit(worktree_path, "three.txt", "three\n", "feat: three")


def history_refs(worktree_path, branch):
    """Every working-history ref ``branch`` currently has."""
    result = run_git(
        worktree_path,
        "for-each-ref",
        "--format=%(refname)",
        history_ref_prefix(branch),
    )
    return [line for line in result.stdout.split("\n") if line]
