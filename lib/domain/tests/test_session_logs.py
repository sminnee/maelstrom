"""The Task session log archive, over a fake transcript root and a fake archive root."""

import io
import os
import tarfile
import tempfile
from pathlib import Path

import pytest

from mael_domain.session_logs import (
    BadSessionPack,
    SessionLogArchive,
    SessionRef,
)

SESSION = "0b5a3c1e-7f2d-4b8e-9a61-3d4c5e6f7a8b"
OTHER = "9c8b7a6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"


@pytest.fixture
def roots(tmp_path) -> tuple[Path, Path]:
    return tmp_path / "projects", tmp_path / "session-logs"


@pytest.fixture
def archive(roots) -> SessionLogArchive:
    transcripts, logs = roots
    return SessionLogArchive(logs, transcripts)


def write_session(transcripts: Path, slug: str, session: str, text: str) -> Path:
    """A Claude session under ``slug``: its JSONL and one subagent transcript."""
    source = transcripts / slug / f"{session}.jsonl"
    (source.parent / session / "subagents").mkdir(parents=True, exist_ok=True)
    source.write_text(text)
    (source.parent / session / "subagents" / "agent-1.jsonl").write_text("sub\n")
    return source


def age(path: Path, seconds: int) -> None:
    """Move ``path``'s modification time back by ``seconds``."""
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns - seconds * 10**9))


def test_an_archive_copies_the_session_file_and_its_directory_from_any_slug(
    roots, archive
):
    transcripts, logs = roots
    write_session(transcripts, "-home-dev-alpha", SESSION, "one\n")
    write_session(transcripts, "-home-dev-bravo", OTHER, "two\n")

    copied = archive.archive("acme", "ACME-3", [SessionRef(SESSION), SessionRef(OTHER)])

    task_dir = logs / "acme" / "ACME-3"
    assert copied == [SESSION, OTHER]
    assert (task_dir / f"{SESSION}.jsonl").read_text() == "one\n"
    assert (task_dir / f"{OTHER}.jsonl").read_text() == "two\n"
    assert (task_dir / SESSION / "subagents" / "agent-1.jsonl").read_text() == "sub\n"


def test_a_current_copy_writes_nothing(roots, archive):
    transcripts, _logs = roots
    write_session(transcripts, "-w", SESSION, "one\n")
    archive.archive("acme", "ACME-3", [SessionRef(SESSION)])

    assert archive.archive("acme", "ACME-3", [SessionRef(SESSION)]) == []


@pytest.mark.parametrize(
    ("text", "copy_is_older"),
    [("one\ntwo\n", False), ("ONE\n", True)],
    ids=["the-source-grew", "the-source-is-newer-at-the-same-size"],
)
def test_a_source_that_changed_since_its_copy_replaces_it(
    roots, archive, text, copy_is_older
):
    transcripts, logs = roots
    source = write_session(transcripts, "-w", SESSION, "one\n")
    archive.archive("acme", "ACME-3", [SessionRef(SESSION)])
    copy = logs / "acme" / "ACME-3" / f"{SESSION}.jsonl"
    source.write_text(text)
    # Each case differs from the copy in one way only: its size, or its time.
    mtime = source.stat().st_mtime_ns
    os.utime(copy, ns=(mtime, mtime - 10**9 if copy_is_older else mtime))

    assert archive.archive("acme", "ACME-3", [SessionRef(SESSION)]) == [SESSION]
    assert copy.read_text() == text


def test_a_session_whose_source_is_gone_keeps_its_copy(roots, archive):
    transcripts, logs = roots
    source = write_session(transcripts, "-w", SESSION, "one\n")
    archive.archive("acme", "ACME-3", [SessionRef(SESSION)])
    source.unlink()

    assert archive.archive("acme", "ACME-3", [SessionRef(SESSION)]) == []
    assert (logs / "acme" / "ACME-3" / f"{SESSION}.jsonl").read_text() == "one\n"


def test_a_pack_stored_on_another_archive_is_the_same_log(roots, archive, tmp_path):
    transcripts, _logs = roots
    write_session(transcripts, "-w", SESSION, "one\n")
    parent = SessionLogArchive(tmp_path / "parent-logs", tmp_path / "none")

    pack = archive.pack(SessionRef(SESSION))
    assert pack is not None
    parent.store_pack("acme", "ACME-3", SESSION, pack)

    task_dir = tmp_path / "parent-logs" / "acme" / "ACME-3"
    assert (task_dir / f"{SESSION}.jsonl").read_text() == "one\n"
    assert (task_dir / SESSION / "subagents" / "agent-1.jsonl").read_text() == "sub\n"


def tar_of(members: dict[str, bytes | None]) -> bytes:
    """A tar of ``members``: a file per name, or a symlink for ``None``."""
    buffer = io.BytesIO()
    with (
        tempfile.TemporaryDirectory() as scratch,
        tarfile.open(fileobj=buffer, mode="w") as tar,
    ):
        for name, body in members.items():
            path = Path(scratch) / name.replace("/", "_")
            if body is None:
                path.symlink_to("/etc/passwd")
            else:
                path.write_bytes(body)
            tar.add(path, arcname=name)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("session_id", "pack"),
    [
        (SESSION, tar_of({f"{OTHER}.jsonl": b"two\n"})),
        (SESSION, tar_of({f"{SESSION}/agent-1.jsonl": b"sub\n"})),
        (SESSION, tar_of({f"{SESSION}.jsonl": b"one\n", f"{SESSION}/x": None})),
        (SESSION, b"not a tar"),
        ("../escape", tar_of({"../escape.jsonl": b"one\n"})),
    ],
    ids=["another-session", "no-jsonl", "a-symlink", "not-a-tar", "unsafe-id"],
)
def test_a_pack_that_is_not_one_sessions_files_is_refused(
    roots, archive, session_id, pack
):
    _transcripts, logs = roots
    with pytest.raises(BadSessionPack):
        archive.store_pack("acme", "ACME-3", session_id, pack)
    task_dir = logs / "acme" / "ACME-3"
    assert not task_dir.exists() or [p.name for p in task_dir.iterdir()] == []
