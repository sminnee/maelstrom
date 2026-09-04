"""Leaf utilities shared across the maelstrom core.

Pure helpers with no Click dependency and no domain knowledge: a UTC timestamp
formatter, an atomic JSON writer, the locked-file transaction primitive, a
permission-tightening helper, and the ``--content-file`` reader. These were
previously duplicated or scattered across ``task.py``, ``session_cli.py``,
``env.py``, and ``worktree.py``; this is their single home.
"""

import fcntl
import json
import os
import stat
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def read_content_file(content_file: str | None) -> str:
    """Read a ``--content-file`` argument's contents.

    ``-`` reads from stdin (the Unix ``cat -`` idiom), so a caller can pipe
    content without managing a temp file; any other value is a path that must
    exist. ``None`` means "no content supplied" and yields an empty string.

    Raises ``FileNotFoundError`` when the path is not a readable file. Callers in
    the CLI layer convert that to a :class:`click.ClickException` — this helper
    stays Click-free so it can live here.
    """
    if content_file is None:
        return ""
    if content_file == "-":
        return sys.stdin.read()
    path = Path(content_file)
    if not path.is_file():
        raise FileNotFoundError(content_file)
    return path.read_text()


def error_text(exc: Exception) -> str:
    """The message *exc* carries, ready to show a user.

    ``str()`` on a ``KeyError`` renders its argument with ``repr``, so a domain
    error raised as ``KeyError("No such task")`` prints with quotes around it.
    Every layer that turns a domain error into a ``ClickException`` or an echo
    needs the bare message, so it comes from here rather than from a workaround
    repeated at each call site.

    Args:
        exc: The exception to describe.

    Returns:
        The message. A ``KeyError`` with no argument yields an empty string.
    """
    if isinstance(exc, KeyError):
        return str(exc.args[0]) if exc.args else ""
    return str(exc)


def abbreviate_home(path: Path, home: Path | None = None) -> str:
    """Render a path with the home directory shown as ``~``.

    A path outside the home directory is returned unchanged. ``home`` is an
    argument rather than a lookup so a caller can test the formatting without
    patching ``Path.home``; it defaults to the real home directory.
    """
    if home is None:
        home = Path.home()
    try:
        return str(Path("~") / path.relative_to(home))
    except ValueError:
        return str(path)


def now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string.

    Byte-identical to the previous ``_now_iso`` helpers in ``task.py`` and
    ``session_cli.py``; keep the exact ``datetime.now(timezone.utc).isoformat()``
    body so serialized timestamps round-trip unchanged.
    """
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(
    path: Path, data: Any, *, indent: int = 2, sort_keys: bool = True
) -> None:
    """Write ``data`` to ``path`` as JSON atomically.

    Writes to a sibling ``.tmp`` file then ``os.replace``s it into place so a
    crash mid-write can never leave a truncated file. Parent directories are
    created as needed. The ``indent=2, sort_keys=True`` defaults match the
    previous ``session_cli._atomic_write_json`` so existing files round-trip
    identically.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=indent, sort_keys=sort_keys)
    os.replace(tmp, path)


def harden_path(path: Path, mode: int) -> bool:
    """Tighten *path*'s permissions to *mode* if it currently exposes extra bits.

    Only ever **narrows** permissions: if ``st_mode & 0o777`` contains any bit
    not present in *mode* (``st_mode & 0o777 & ~mode``), the path is chmod-ed to
    *mode* and ``True`` is returned. If the path is already at or tighter than
    *mode*, nothing is changed and ``False`` is returned. This guarantees we
    never relax a user's intentionally-tighter permissions.

    Args:
        path: File or directory to inspect.
        mode: The target mode (e.g. ``0o600`` for files, ``0o700`` for dirs).

    Returns:
        True if the path was loosened and has now been tightened, else False.

    Raises:
        OSError: If the path cannot be stat-ed or chmod-ed (caller decides how
            to surface this; load paths swallow it, doctor reports it).
    """
    current = stat.S_IMODE(os.stat(path).st_mode)
    if current & ~mode:
        os.chmod(path, mode)
        return True
    return False


# --- Locked file transactions -------------------------------------------------


class _Txn:
    """Buffer for a :func:`locked_file` transaction.

    ``text`` starts as the file's current contents; assigning to it buffers a
    rewrite that is flushed only on clean exit of the ``with`` block, and only
    if the value actually changed.
    """

    def __init__(self, initial: str) -> None:
        self._initial = initial
        self.text = initial


@contextmanager
def locked_file(
    path: Path, *, timeout: float = 10.0, create: bool = True, mode: int = 0o600
) -> Iterator[_Txn]:
    """Open *path* under an exclusive advisory lock as a read/rewrite transaction.

    The file is its own lockfile: we ``flock`` its open fd directly (no separate
    lockfile, no atomic rename). The yielded transaction exposes the current
    ``text``; assigning ``txn.text`` buffers a rewrite that is flushed in place on
    clean exit, and only when the contents changed. On an exception inside the
    block nothing is written. The lock is always released in ``finally``.

    Because we lock the target fd, the rewrite is truncate-in-place rather than
    temp+``os.replace`` — replacing the inode would orphan a second waiter's
    already-open fd. **Do not "simplify" this into ``write_text`` + ``chmod``:**
    that both breaks the locking contract and reopens the secret-bytes race the
    fd-fchmod below closes.

    Permission guarantee: on every clean exit we ``os.fchmod`` the locked fd to
    *mode* (default ``0o600``) — so an existing loose file is tightened even on a
    no-op transaction, and the mode is set on the fd while we hold the lock,
    *before* any secret content is written. ``open(path, "a+")`` creates the file
    empty under the process umask; the only default-umask window is for that
    empty, secret-free file, which we ``fchmod`` to *mode* before returning the
    transaction. When we create the file, the parent directory is also tightened
    to ``0o700`` (narrow-only) so the whole secret tree is non-world-readable.

    Args:
        path: File to lock and (optionally) rewrite.
        timeout: Seconds to wait for the lock before raising ``TimeoutError``.
        create: Create the file if missing (open ``a+`` never truncates).
        mode: Permission bits to enforce on the file's fd (default ``0o600``).

    Raises:
        TimeoutError: If the lock cannot be acquired within *timeout* seconds.
        FileNotFoundError: If the file is missing and *create* is False.
    """
    if not create and not path.exists():
        raise FileNotFoundError(path)

    created = not path.exists()

    # "a+" creates the file if missing and never truncates on open. Seek to 0 to
    # read existing contents.
    fd = open(path, "a+")
    try:
        deadline = time.monotonic() + timeout
        # Non-blocking acquire + sleep-poll so the deadline is portable; a plain
        # blocking LOCK_EX can't be time-bounded across platforms. Mirrors
        # task_store._locked.
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"{path} is locked by another process; "
                        f"gave up after {int(timeout)}s."
                    )
                time.sleep(0.1)

        # Set the mode on the held fd before any (potentially secret) content is
        # written. We created the file empty above, so no secret is exposed
        # during the brief default-umask window.
        os.fchmod(fd.fileno(), mode)

        fd.seek(0)
        txn = _Txn(initial=fd.read())
        try:
            yield txn
            if txn.text != txn._initial:
                fd.seek(0)
                fd.truncate()
                fd.write(txn.text)
                fd.flush()
            # Re-assert the mode on every clean exit so an existing loose file
            # is tightened even when the contents did not change.
            os.fchmod(fd.fileno(), mode)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        fd.close()

    # Only narrow the parent dir, and only when we created the file — never widen
    # or stomp an intentionally-shared directory mode.
    if created:
        try:
            harden_path(path.parent, 0o700)
        except OSError:
            pass
