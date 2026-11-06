"""The record of the last **Update job**."""

import fcntl
import os

import pytest

from mael_domain.release_bundle.update_job import (
    UpdateRecord,
    lock_held,
    lock_path,
    read_record,
    record_path,
    update_lock,
    write_record,
)

RECORD = UpdateRecord(
    state="succeeded",
    pid=12,
    started_at="2026-11-06T01:00:00+00:00",
    finished_at="2026-11-06T01:04:00+00:00",
    log="/home/u/.maelstrom/logs/update.log",
    from_tag="v1",
    to_tag="v2",
    exit_code=0,
)


def test_a_record_reads_back_as_written(tmp_path):
    write_record(tmp_path, RECORD)

    assert read_record(tmp_path) == RECORD
    assert [p.name for p in tmp_path.iterdir()] == ["update.json"]


def test_no_file_is_no_record(tmp_path):
    assert read_record(tmp_path) is None


def test_a_broken_file_is_no_record(tmp_path):
    record_path(tmp_path).write_text('{"state": "runn')

    assert read_record(tmp_path) is None


def test_a_failed_write_leaves_the_old_record_whole(tmp_path, monkeypatch):
    write_record(tmp_path, RECORD)

    def no_rename(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", no_rename)
    with pytest.raises(OSError):
        write_record(tmp_path, UpdateRecord("running", 13, "t", "log"))

    monkeypatch.undo()
    assert read_record(tmp_path) == RECORD


def test_running_with_its_lock_held_is_running(tmp_path):
    write_record(tmp_path, UpdateRecord("running", 13, "t", "log"))

    with update_lock(tmp_path) as locked:
        assert locked
        assert read_record(tmp_path).state == "running"


def test_running_with_its_lock_free_is_interrupted(tmp_path):
    """A reboot frees the lock; the pid may now name another process."""
    write_record(tmp_path, UpdateRecord("running", os.getpid(), "t", "log"))

    assert read_record(tmp_path).state == "interrupted"


def test_the_lock_is_held_while_another_holder_has_it(tmp_path):
    assert not lock_held(tmp_path)
    with open(lock_path(tmp_path), "w") as other:
        fcntl.flock(other, fcntl.LOCK_EX)

        assert lock_held(tmp_path)
        with update_lock(tmp_path, sleep=lambda _: None) as locked:
            assert not locked

    assert not lock_held(tmp_path)
