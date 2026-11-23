"""The audit log writer: one line per record, owner-only, rolled over by size."""

import json
import logging
import stat

from mael_common.audit_log import AuditLog


def _numbers(path) -> list[int]:
    return [json.loads(line)["n"] for line in path.read_text().splitlines()]


def test_a_full_log_rolls_over_and_keeps_only_its_backups(tmp_path):
    path = tmp_path / "audit.jsonl"
    # Each record is about 50 bytes, so two fill the file.
    audit = AuditLog(path, max_bytes=110, backups=2)
    for n in range(7):
        audit.write({"n": n})

    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "audit.jsonl",
        "audit.jsonl.1",
        "audit.jsonl.2",
    ]
    assert _numbers(path) == [6]
    assert _numbers(tmp_path / "audit.jsonl.1") == [4, 5]
    assert _numbers(tmp_path / "audit.jsonl.2") == [2, 3]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_record_that_cannot_be_written_logs_a_warning_and_does_not_raise(
    tmp_path, caplog
):
    # A file where the log's directory should be: every write fails.
    (tmp_path / "blocked").write_text("")
    audit = AuditLog(tmp_path / "blocked" / "audit.jsonl")

    with caplog.at_level(logging.WARNING, logger="mael_common.audit_log"):
        audit.write({"n": 1})

    assert "could not write the audit record" in caplog.text
