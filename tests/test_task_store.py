"""Tests for the GitFileStore backend."""

import subprocess

from maelstrom.task_store import GitFileStore


def _git(root, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


class TestGitFileStore:
    def test_write_read_round_trip(self, tmp_path):
        store = GitFileStore(root=tmp_path / "tasks")
        store.write("p/todo/x.md", "hello", message="task: add x")
        assert store.read("p/todo/x.md") == "hello"
        assert store.exists("p/todo/x.md")

    def test_read_missing_returns_none(self, tmp_path):
        store = GitFileStore(root=tmp_path / "tasks")
        assert store.read("p/todo/missing.md") is None
        assert not store.exists("p/todo/missing.md")

    def test_write_produces_commit(self, tmp_path):
        root = tmp_path / "tasks"
        store = GitFileStore(root=root)
        store.write("p/todo/x.md", "hello", message="task: add x")

        # .git exists
        assert (root / ".git").exists()
        # at least one commit, with our message
        log = _git(root, "log", "--oneline")
        assert log.strip()
        assert "task: add x" in _git(root, "log", "-1", "--pretty=%s")
        # working tree is clean
        assert _git(root, "status", "--porcelain").strip() == ""

    def test_list_dir_filters_by_prefix(self, tmp_path):
        store = GitFileStore(root=tmp_path / "tasks")
        store.write("p/todo/a.md", "a", message="m")
        store.write("p/done/b.md", "b", message="m")
        store.write("other/todo/c.md", "c", message="m")
        keys = set(store.list_dir("p/"))
        assert keys == {"p/todo/a.md", "p/done/b.md"}

    def test_list_dir_excludes_git(self, tmp_path):
        store = GitFileStore(root=tmp_path / "tasks")
        store.write("p/todo/a.md", "a", message="m")
        assert all(not k.startswith(".git") for k in store.list_dir(""))

    def test_delete_produces_commit(self, tmp_path):
        root = tmp_path / "tasks"
        store = GitFileStore(root=root)
        store.write("p/todo/x.md", "hello", message="task: add x")
        store.delete("p/todo/x.md", message="task: remove x")
        assert not store.exists("p/todo/x.md")
        assert "task: remove x" in _git(root, "log", "-1", "--pretty=%s")
        assert _git(root, "status", "--porcelain").strip() == ""

    def test_commit_noop_when_nothing_staged(self, tmp_path):
        root = tmp_path / "tasks"
        store = GitFileStore(root=root)
        store.write("p/todo/x.md", "hello", message="first")
        count_before = len(_git(root, "log", "--oneline").strip().splitlines())
        # Writing the identical content stages nothing -> no new commit.
        store.write("p/todo/x.md", "hello", message="second")
        count_after = len(_git(root, "log", "--oneline").strip().splitlines())
        assert count_after == count_before
