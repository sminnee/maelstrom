"""Storage layer for the task notebook.

Tasks are stored as a flat key->text store where keys are POSIX-style relative
paths of the form ``<project>/<status>/<id>.md``. The folder is the status.

Two backends are provided:

- ``InMemoryStore`` — a ``dict``-backed store with no git or filesystem; used by
  the task unit-test suite for fast, deterministic tests.
- ``GitFileStore`` — maps keys to files under a root directory and commits every
  mutation to a git repo (lazily initialised), so the task notebook is fully
  versioned.

``GitFileStore`` also supports :meth:`~TaskStore.transaction`, batching several
mutations into a single commit (with true rollback on error), and serialises all
commit-producing paths across processes with a ``fcntl`` file lock so concurrent
``mael`` processes can't interleave commits or trip over git's index. Reads are
never locked.
"""

import fcntl
import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Protocol

from .context import get_maelstrom_dir


class TaskStore(Protocol):
    """A flat key->text store.

    Keys are POSIX relative paths (``<project>/<status>/<id>.md``). The
    ``message`` argument on mutating operations is the commit subject for
    backends that version their contents. It is optional — inside a
    :meth:`transaction` the per-call message is ignored (the transaction owns the
    single commit subject), so callers batching mutations need not supply one.
    """

    def list_dir(self, prefix: str) -> list[str]:
        """Return all keys that start with ``prefix``."""
        ...

    def read(self, key: str) -> str | None:
        """Return the text stored at ``key``, or ``None`` if it does not exist."""
        ...

    def write(self, key: str, text: str, *, message: str | None = None) -> None:
        """Store ``text`` at ``key``, recording ``message`` as the commit subject.

        ``message`` is ignored (and may be omitted) inside a :meth:`transaction`.
        """
        ...

    def delete(self, key: str, *, message: str | None = None) -> None:
        """Remove ``key``, recording ``message`` as the commit subject.

        ``message`` is ignored (and may be omitted) inside a :meth:`transaction`.
        """
        ...

    def exists(self, key: str) -> bool:
        """Return whether ``key`` exists."""
        ...

    def transaction(self, *, message: str) -> AbstractContextManager[None]:
        """Batch all mutations in the ``with`` block into a single commit.

        Versioned backends defer their commit to block-exit and use ``message`` as the
        single subject; the per-call ``message`` on write/delete is ignored inside the
        block. On an exception the block is rolled back (no commit, no partial change).
        Non-versioned backends treat this as a no-op context manager.
        """
        ...


class InMemoryStore:
    """A ``dict``-backed :class:`TaskStore` with no git or filesystem."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def list_dir(self, prefix: str) -> list[str]:
        return [k for k in self._data if k.startswith(prefix)]

    def read(self, key: str) -> str | None:
        return self._data.get(key)

    def write(self, key: str, text: str, *, message: str | None = None) -> None:
        self._data[key] = text

    def delete(self, key: str, *, message: str | None = None) -> None:
        self._data.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self._data

    @contextmanager
    def transaction(self, *, message: str) -> Iterator[None]:
        """No-op: an in-memory store has no commits to batch."""
        yield


_LOCK_TIMEOUT = 60.0


class GitFileStore:
    """A :class:`TaskStore` backed by files under ``root``, versioned with git.

    The git repo is initialised lazily on first mutation, with a local
    ``user.name``/``user.email`` so it works in CI environments without global
    git config. Every ``write``/``delete`` stages all changes and commits with
    the provided message; a commit with nothing staged is a no-op (not an error).

    Two safeguards layer on top of the basic write/commit:

    - :meth:`transaction` batches several mutations into a single commit. The
      filesystem is still mutated eagerly so reads inside the block see a
      consistent view; only the commit is deferred to the outermost block exit.
      An exception inside the block rolls the repo back to its pre-transaction
      state (no commit, no partial change) and re-raises.
    - Every commit-producing path (a standalone ``write``/``delete`` or a whole
      ``transaction``) is serialised across processes with an exclusive
      ``fcntl.flock`` on ``<root>/.writelock``. ``mael task`` runs as independent
      short-lived processes sharing one task store, and the lock stops concurrent
      mutators from interleaving commits or fighting over git's index. The lock
      blocks up to ``_LOCK_TIMEOUT`` seconds, then raises ``TimeoutError``. The OS
      drops the lock when the holding process dies, so a crashed holder frees it
      automatically — no stale-marker scheme is needed. Reads are never locked.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else get_maelstrom_dir() / "tasks"
        self._txn_depth = 0
        self._txn_message: str | None = None
        self._lock_fd: int | None = None

    # --- private git helpers (quiet, non-raising) ---

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        """Run a git command in ``root``, quietly, without raising on failure."""
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=False,
            capture_output=True,
            text=True,
        )

    def _ensure_repo(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not (self.root / ".git").exists():
            self._git("init")
            self._git("config", "user.name", "maelstrom")
            self._git("config", "user.email", "maelstrom@localhost")
        # Keep the cross-process lock handle out of every ``git add -A`` via the
        # repo-local exclude file rather than a tracked ``.gitignore`` — that way
        # the working tree stays free of any infrastructure file (nothing for a
        # rollback's ``git clean`` to have to special-case).
        exclude = self.root / ".git" / "info" / "exclude"
        if exclude.parent.is_dir() and ".writelock" not in self._read_or_empty(exclude):
            with exclude.open("a") as fh:
                fh.write(".writelock\n")

    @staticmethod
    def _read_or_empty(path: Path) -> str:
        try:
            return path.read_text()
        except (FileNotFoundError, OSError):
            return ""

    def _commit(self, message: str) -> None:
        self._git("add", "-A")
        # Commit only if there is something staged; otherwise this is a no-op.
        if self._git("diff", "--cached", "--quiet").returncode != 0:
            self._git("commit", "-m", message, "--no-verify")

    def _path(self, key: str) -> Path:
        return self.root / key

    # --- cross-process locking ---

    def _lock_path(self) -> Path:
        return self.root / ".writelock"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Hold an exclusive cross-process lock for the block.

        Re-entrant within this instance: if the lock is already held (we're inside
        a transaction), the block runs without re-acquiring. Blocks up to
        ``_LOCK_TIMEOUT`` seconds for a contended lock, then raises ``TimeoutError``.
        """
        if self._lock_fd is not None:  # already held (inside a transaction) — re-enter
            yield
            return
        self._ensure_repo()
        fd = os.open(self._lock_path(), os.O_CREAT | os.O_RDWR, 0o644)
        deadline = time.monotonic() + _LOCK_TIMEOUT
        # Non-blocking acquire + sleep-poll so we can enforce a portable deadline;
        # a plain blocking LOCK_EX can't be time-bounded across platforms.
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise TimeoutError(
                        "Task store is locked by another mael process; "
                        f"gave up after {int(_LOCK_TIMEOUT)}s."
                    )
                time.sleep(0.1)
        self._lock_fd = fd
        try:
            yield
        finally:
            self._lock_fd = None
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    # --- transactions ---

    def _head(self) -> str | None:
        """Return the current HEAD sha, or ``None`` on a repo with no commits."""
        r = self._git("rev-parse", "--verify", "HEAD")
        return r.stdout.strip() if r.returncode == 0 else None

    def _rollback(self, saved_head: str | None) -> None:
        """Restore the repo to ``saved_head`` (or empty), discarding all changes."""
        if saved_head is None:
            # No commit existed at txn start: drop the index and every change made
            # during the txn. ``.writelock`` is excluded (via .git/info/exclude),
            # so a plain ``-fd`` clean leaves it alone; we keep the path stable
            # even though the open lock fd would survive an unlink.
            self._git("read-tree", "--empty")
            self._git("clean", "-fd", "--", ".")
        else:
            self._git("reset", "--hard", saved_head)
            self._git("clean", "-fd")  # remove files created during the txn

    @contextmanager
    def transaction(self, *, message: str) -> Iterator[None]:
        """Batch all mutations in the block into a single commit (see class docs)."""
        with self._locked():
            failed = False
            if self._txn_depth == 0:
                self._txn_message = message
                saved_head = self._head()  # None on a repo with no commits yet
            self._txn_depth += 1
            try:
                yield
            except BaseException:
                if self._txn_depth == 1:
                    self._rollback(saved_head)
                    failed = True
                raise
            finally:
                self._txn_depth -= 1
                if self._txn_depth == 0:
                    msg = self._txn_message
                    self._txn_message = None
                    # Exactly one of {commit, rollback} runs per outermost txn.
                    if not failed and msg is not None:
                        self._commit(msg)

    def _maybe_commit(self, message: str | None) -> None:
        """Commit now, unless a transaction is open (then defer to its exit).

        Outside a transaction a ``message`` is required; a bare ``write``/``delete``
        with no message falls back to a generic subject rather than producing an
        unlabelled commit.
        """
        if self._txn_depth == 0:
            self._commit(message or "task: update")

    # --- TaskStore protocol ---

    def list_dir(self, prefix: str) -> list[str]:
        if not self.root.is_dir():
            return []
        keys: list[str] = []
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(self.root).as_posix()
            if rel.startswith(".git/") or rel == ".git":
                continue
            # Skip the cross-process lock handle — it's infrastructure, not a key.
            if rel == ".writelock":
                continue
            if rel.startswith(prefix):
                keys.append(rel)
        return keys

    def read(self, key: str) -> str | None:
        path = self._path(key)
        try:
            return path.read_text()
        except (FileNotFoundError, OSError):
            return None

    def write(self, key: str, text: str, *, message: str | None = None) -> None:
        with self._locked():
            self._ensure_repo()
            path = self._path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            self._maybe_commit(message)

    def delete(self, key: str, *, message: str | None = None) -> None:
        with self._locked():
            self._ensure_repo()
            path = self._path(key)
            try:
                path.unlink()
            except FileNotFoundError:
                return
            self._maybe_commit(message)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()
