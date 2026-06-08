"""Tests for the GitFileStore backend."""

import multiprocessing
import subprocess

import pytest

import maelstrom.task_store as task_store
from maelstrom.task_store import GitFileStore


def _git(root, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _commit_count(root) -> int:
    """Number of commits in ``root`` (0 if the repo has no commits yet)."""
    r = subprocess.run(
        ["git", "-C", str(root), "log", "--oneline"],
        check=False,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return 0
    out = r.stdout.strip()
    return len(out.splitlines()) if out else 0


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


class TestTransaction:
    def test_transaction_single_commit_for_multiple_writes(self, tmp_path):
        root = tmp_path / "tasks"
        store = GitFileStore(root=root)
        store.write("p/todo/seed.md", "seed", message="task: seed")
        before = _commit_count(root)
        with store.transaction(message="task: batch"):
            store.write("p/todo/a.md", "a", message="ignored-a")
            store.write("p/todo/b.md", "b", message="ignored-b")
        assert _commit_count(root) == before + 1
        assert "task: batch" in _git(root, "log", "-1", "--pretty=%s")
        assert store.read("p/todo/a.md") == "a"
        assert store.read("p/todo/b.md") == "b"
        assert _git(root, "status", "--porcelain").strip() == ""

    def test_transaction_batches_write_and_delete(self, tmp_path):
        root = tmp_path / "tasks"
        store = GitFileStore(root=root)
        store.write("p/todo/x.md", "x", message="task: add x")
        before = _commit_count(root)
        # Mirrors move(): write the new key, delete the old one, one commit.
        with store.transaction(message="task: move x"):
            store.write("p/done/x.md", "x", message="ignored")
            store.delete("p/todo/x.md", message="ignored")
        assert _commit_count(root) == before + 1
        assert store.exists("p/done/x.md")
        assert not store.exists("p/todo/x.md")
        assert _git(root, "status", "--porcelain").strip() == ""

    def test_empty_transaction_makes_no_commit(self, tmp_path):
        root = tmp_path / "tasks"
        store = GitFileStore(root=root)
        store.write("p/todo/x.md", "x", message="task: add x")
        before = _commit_count(root)
        with store.transaction(message="task: nothing"):
            pass
        assert _commit_count(root) == before

    def test_exception_rolls_back_and_makes_no_commit(self, tmp_path):
        root = tmp_path / "tasks"
        store = GitFileStore(root=root)
        store.write("p/todo/x.md", "x", message="task: add x")
        before = _commit_count(root)
        with pytest.raises(RuntimeError, match="boom"):
            with store.transaction(message="task: doomed"):
                store.write("p/todo/new.md", "new", message="ignored")
                raise RuntimeError("boom")
        assert _commit_count(root) == before
        assert not store.exists("p/todo/new.md")
        assert store.read("p/todo/x.md") == "x"
        assert _git(root, "status", "--porcelain").strip() == ""

    def test_exception_rollback_fresh_repo(self, tmp_path):
        root = tmp_path / "tasks"
        store = GitFileStore(root=root)
        # No prior commit: exercises the saved_head is None rollback branch.
        with pytest.raises(RuntimeError, match="boom"):
            with store.transaction(message="task: doomed"):
                store.write("p/todo/new.md", "new", message="ignored")
                raise RuntimeError("boom")
        assert _commit_count(root) == 0
        assert not store.exists("p/todo/new.md")
        assert _git(root, "status", "--porcelain").strip() == ""

    def test_writelock_excluded(self, tmp_path):
        root = tmp_path / "tasks"
        store = GitFileStore(root=root)
        store.write("p/todo/x.md", "x", message="task: add x")
        # The lock handle is excluded via the repo-local exclude file...
        assert ".writelock" in (root / ".git" / "info" / "exclude").read_text()
        # ...so it never shows up as an untracked/staged change.
        assert ".writelock" not in _git(root, "status", "--porcelain")
        # ...and never as a task key.
        assert ".writelock" not in store.list_dir("")


class TestTransactionViaModel:
    def test_move_via_model_is_single_commit(self, tmp_path):
        import maelstrom.task as task

        root = tmp_path / "tasks"
        store = GitFileStore(root=root)
        created = task.create(store, project="p", title="thing")
        before = _commit_count(root)
        task.move(store, "p", created.id, task.STATUS_DONE)
        assert _commit_count(root) == before + 1
        assert "task: move" in _git(root, "log", "-1", "--pretty=%s")
        assert _git(root, "status", "--porcelain").strip() == ""

    def test_delete_via_model_is_single_commit(self, tmp_path):
        import maelstrom.task as task

        root = tmp_path / "tasks"
        store = GitFileStore(root=root)
        a = task.create(store, project="p", title="dep")
        task.create(store, project="p", title="needs A", follows=[a.id])
        before = _commit_count(root)
        # 1 delete + 1 dependent rewrite -> exactly one commit.
        task.delete(store, "p", a.id)
        assert _commit_count(root) == before + 1
        assert f"task: rm {a.id}" in _git(root, "log", "-1", "--pretty=%s")
        assert _git(root, "status", "--porcelain").strip() == ""


def _writer_worker(root_str: str, prefix: str, count: int) -> None:
    """Module-level worker (picklable) that writes ``count`` files to the store."""
    from pathlib import Path

    from maelstrom.task_store import GitFileStore

    store = GitFileStore(root=Path(root_str))
    for i in range(count):
        store.write(f"p/todo/{prefix}-{i}.md", f"{prefix}-{i}", message=f"add {prefix}-{i}")


class TestLocking:
    @pytest.mark.slow
    def test_lock_serialises_concurrent_writers(self, tmp_path):
        root = tmp_path / "tasks"
        # Initialise the repo once so both children share a ready store.
        GitFileStore(root=root).write("p/todo/seed.md", "seed", message="seed")
        per_proc = 8
        ctx = multiprocessing.get_context("spawn")
        procs = [
            ctx.Process(target=_writer_worker, args=(str(root), prefix, per_proc))
            for prefix in ("alpha", "bravo")
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=60)
            assert p.exitcode == 0
        # seed + 2 writers * per_proc commits, tree clean, no corruption.
        assert _commit_count(root) == 1 + 2 * per_proc
        assert _git(root, "status", "--porcelain").strip() == ""

    def test_lock_times_out(self, tmp_path, monkeypatch):
        root = tmp_path / "tasks"
        # Short timeout so the test is fast.
        monkeypatch.setattr(task_store, "_LOCK_TIMEOUT", 0.5)
        holder = GitFileStore(root=root)
        # Acquire and hold the lock via the holder's _locked() context.
        with holder._locked():
            other = GitFileStore(root=root)
            with pytest.raises(TimeoutError, match="locked by another mael process"):
                other.write("p/todo/x.md", "x", message="task: add x")
