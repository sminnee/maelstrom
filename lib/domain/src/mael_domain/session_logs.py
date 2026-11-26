"""Task session logs: the archived copy of each of a task's agent sessions.

See ``docs/specs/tasks/CONTEXT.md``, "Task session log". The source is the
harness's own session file. For Claude, that is
``<transcript root>/<slug>/<session-id>.jsonl`` and its ``<session-id>/``
directory, which holds the subagent transcripts and the stored tool results.

The layout of the archive is
``<archive root>/<project>/<task id>/<session-id>.jsonl`` and
``<archive root>/<project>/<task id>/<session-id>/``.

The rules:

- A session whose source is newer than its copy, or has a different size, is
  copied again. A session whose copy is current is not written.
- An archive never deletes a copy. A session whose source is gone keeps its
  copy.
- The ``.jsonl`` is written last, through a temporary name and a rename. So a
  reader never sees half a file, and a current ``.jsonl`` says that its
  directory is complete too.
- A copy of a session that is still running can end on a half-written
  line. A reader skips a last line that does not parse.
"""

import io
import os
import shutil
import tarfile
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mael_agent.harness_model import HARNESS_CLAUDE


@dataclass(frozen=True)
class SessionRef:
    """One session of a task: the harness's own id, and the harness."""

    id: str
    harness: str = HARNESS_CLAUDE


@dataclass(frozen=True)
class SessionLog:
    """What the archive holds for one session of a task.

    ``path`` is the copy of the session file, and ``None`` when the session
    has no log. ``size`` counts the session file and its directory.
    """

    session_id: str
    harness: str
    path: Path | None
    size: int = 0
    archived_at: str = ""

    @property
    def archived(self) -> bool:
        return self.path is not None

    def to_json(self) -> dict[str, object]:
        if self.path is None:
            return {
                "sessionId": self.session_id,
                "harness": self.harness,
                "archived": False,
            }
        return {
            "sessionId": self.session_id,
            "harness": self.harness,
            "archived": True,
            "path": str(self.path),
            "size": self.size,
            "archivedAt": self.archived_at,
        }


class BadSessionPack(ValueError):
    """A pushed pack that does not hold one session's files, and only those."""


class SessionLogArchive:
    """The archive of Task session logs, over injected roots.

    ``archive_root`` is :func:`~mael_domain.notebook_root.session_log_root`. ``transcript_root``
    is where the harness writes its session files: for Claude,
    :func:`~mael_common.claude_paths.get_transcript_root`.
    """

    def __init__(self, archive_root: Path, transcript_root: Path) -> None:
        self.archive_root = archive_root
        self.transcript_root = transcript_root

    # -- reads --

    def task_dir(self, project: str, task_id: str) -> Path:
        return self.archive_root / project / task_id

    def source(self, session: SessionRef) -> Path | None:
        """The harness's file of ``session``, under any project slug; ``None`` when gone.

        A session id is a UUID, so one glob finds it without the worktree it
        ran in, which can since be closed.
        """
        if session.harness != HARNESS_CLAUDE or not _safe_name(session.id):
            return None
        found = sorted(self.transcript_root.glob(f"*/{session.id}.jsonl"))
        return found[0] if found else None

    def signature(self, session: SessionRef) -> tuple[int, int] | None:
        """The source's modification time and size; ``None`` when it is gone.

        A copy made from this signature is current until the signature changes.
        """
        source = self.source(session)
        if source is None:
            return None
        stat = source.stat()
        return stat.st_mtime_ns, stat.st_size

    def logs(
        self, project: str, task_id: str, sessions: Iterable[SessionRef]
    ) -> list[SessionLog]:
        """One entry for each of ``sessions``, in their order."""
        task_dir = self.task_dir(project, task_id)
        return [_log_of(task_dir, session) for session in sessions]

    # -- writes --

    def archive(
        self, project: str, task_id: str, sessions: Iterable[SessionRef]
    ) -> list[str]:
        """Copy each session whose copy is not current. Returns the ids copied."""
        task_dir = self.task_dir(project, task_id)
        copied: list[str] = []
        for session in sessions:
            source = self.source(session)
            if source is None:
                continue
            target = task_dir / f"{session.id}.jsonl"
            if _current(source, target):
                continue
            task_dir.mkdir(parents=True, exist_ok=True)
            source_dir = source.with_suffix("")
            if source_dir.is_dir():
                shutil.copytree(source_dir, task_dir / session.id, dirs_exist_ok=True)
            _replace(target, lambda tmp, src=source: shutil.copyfile(src, tmp))
            copied.append(session.id)
        return copied

    def pack(self, session: SessionRef) -> bytes | None:
        """A tar of ``session``'s source files, for a push to the task's home.

        ``None`` when the source is gone.
        """
        source = self.source(session)
        if source is None:
            return None
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            source_dir = source.with_suffix("")
            if source_dir.is_dir():
                tar.add(source_dir, arcname=session.id)
            tar.add(source, arcname=f"{session.id}.jsonl")
        return buffer.getvalue()

    def store_pack(
        self, project: str, task_id: str, session_id: str, data: bytes | bytearray
    ) -> None:
        """Store a :meth:`pack` as ``session_id``'s log, in place of an older copy.

        Raises:
            BadSessionPack: The pack is not a tar of that one session's files.
        """
        if not _safe_name(session_id):
            raise BadSessionPack(f"{session_id!r} is not a session id")
        task_dir = self.task_dir(project, task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=task_dir, prefix=".push-") as scratch:
            unpacked = Path(scratch)
            try:
                with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
                    _check_members(tar, session_id)
                    tar.extractall(unpacked, filter="data")
            except tarfile.TarError as exc:
                raise BadSessionPack(str(exc)) from exc
            log_file = unpacked / f"{session_id}.jsonl"
            if not log_file.is_file():
                raise BadSessionPack(f"the pack holds no {session_id}.jsonl")
            if (unpacked / session_id).is_dir():
                shutil.copytree(
                    unpacked / session_id, task_dir / session_id, dirs_exist_ok=True
                )
            # The pack keeps the source's time. The copy's time is its archive time.
            os.utime(log_file)
            os.replace(log_file, task_dir / f"{session_id}.jsonl")


def _safe_name(session_id: str) -> bool:
    """Whether ``session_id`` can name a file: no separator, no glob, no dot name."""
    return (
        bool(session_id)
        and session_id not in {".", ".."}
        and not any(ch in session_id for ch in "/\\*?[]")
    )


def _current(source: Path, copy: Path) -> bool:
    """Whether ``copy`` was made from ``source`` as it is now."""
    if not copy.is_file():
        return False
    src, dst = source.stat(), copy.stat()
    return dst.st_size == src.st_size and dst.st_mtime_ns >= src.st_mtime_ns


def _replace(target: Path, write) -> None:
    """Write ``target`` through a temporary name in its directory, then rename."""
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    os.close(fd)
    try:
        write(tmp)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _check_members(tar: tarfile.TarFile, session_id: str) -> None:
    for member in tar.getmembers():
        name = member.name
        if name != f"{session_id}.jsonl" and name != session_id:
            if not name.startswith(f"{session_id}/"):
                raise BadSessionPack(f"{name} is not a file of session {session_id}")
        if not (member.isfile() or member.isdir()):
            raise BadSessionPack(f"{name} is not a file or a directory")


def _log_of(task_dir: Path, session: SessionRef) -> SessionLog:
    path = task_dir / f"{session.id}.jsonl"
    if not _safe_name(session.id) or not path.is_file():
        return SessionLog(session.id, session.harness, None)
    stat = path.stat()
    size = stat.st_size
    session_dir = task_dir / session.id
    if session_dir.is_dir():
        size += sum(f.stat().st_size for f in session_dir.rglob("*") if f.is_file())
    archived_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
    return SessionLog(session.id, session.harness, path, size, archived_at)
