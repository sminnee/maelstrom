"""A worktree's dirty files, read from real repositories.

The read is a parse of git's own output, so every case builds a repo with
:mod:`git_helpers`: hand-typed porcelain would test the typing.
"""

import asyncio

import pytest
from git_helpers import create_commit, run_git, setup_git_repo

from mael_domain.git_status import DirtyFile, read_dirty_files, read_dirty_files_async


@pytest.fixture
def repo(tmp_path):
    setup_git_repo(tmp_path)
    create_commit(tmp_path, "a.txt", "a\n", "chore: start")
    create_commit(tmp_path, "b.txt", "b\n", "chore: more")
    return tmp_path


def test_a_clean_worktree_has_no_dirty_files(repo):
    assert read_dirty_files(repo) == []


def test_a_directory_that_is_not_a_repo_has_no_dirty_files(tmp_path):
    """A failed ``git status`` reads as none."""
    (tmp_path / "loose.txt").write_text("x\n")

    assert read_dirty_files(tmp_path) == []


def test_staged_unstaged_both_and_untracked_are_split(repo):
    (repo / "a.txt").write_text("a2\n")
    run_git(repo, "add", "a.txt")
    (repo / "b.txt").write_text("b2\n")
    (repo / "both.txt").write_text("1\n")
    run_git(repo, "add", "both.txt")
    (repo / "both.txt").write_text("2\n")
    (repo / "new.txt").write_text("n\n")

    files = {
        f.path: (f.staged, f.unstaged, f.untracked) for f in read_dirty_files(repo)
    }

    assert files == {
        "a.txt": (True, False, False),
        "b.txt": (False, True, False),
        "both.txt": (True, True, False),
        "new.txt": (False, False, True),
    }


def test_each_file_in_an_untracked_directory_is_listed(repo):
    (repo / "new").mkdir()
    (repo / "new" / "one.txt").write_text("1\n")
    (repo / "new" / "deep").mkdir()
    (repo / "new" / "deep" / "two.txt").write_text("2\n")

    assert read_dirty_files(repo) == [
        DirtyFile("new/deep/two.txt", "?", "?"),
        DirtyFile("new/one.txt", "?", "?"),
    ]


def test_a_rename_is_one_file_at_its_new_path(repo):
    run_git(repo, "mv", "a.txt", "renamed.txt")

    assert read_dirty_files(repo) == [DirtyFile("renamed.txt", "R", " ")]


def test_a_path_with_a_space_is_read_whole(repo):
    (repo / "my notes.txt").write_text("x\n")
    run_git(repo, "add", "my notes.txt")
    run_git(repo, "mv", "b.txt", "b two.txt")

    assert sorted(f.path for f in read_dirty_files(repo)) == [
        "b two.txt",
        "my notes.txt",
    ]


def test_env_is_not_a_dirty_file(repo):
    (repo / ".env").write_text("PORT=3030\n")

    assert read_dirty_files(repo) == []


def test_a_missing_directory_has_no_dirty_files(tmp_path):
    assert read_dirty_files(tmp_path / "gone") == []


def test_the_async_form_reads_the_same(repo):
    (repo / "new.txt").write_text("x\n")

    assert asyncio.run(read_dirty_files_async(repo)) == read_dirty_files(repo)
