"""The gate table, fed the lines of a real ``tangier gate status --watch --json``.

``fixtures/gate-watch.jsonl`` is recorded from tangier 0.3.1 in a scratch repo
with three gates: ``ok`` (exits 0), ``bad`` (exits 1) and ``slow``. Job 1100
runs ``ok`` and ``bad`` and fails. Job 1107 runs ``ok`` alone and passes. Job
1112 runs ``slow`` and is cancelled while it is queued. The watch is restarted
after line 12, so line 13 is a second ``status``. ``fixtures/gate-status-after.json``
is ``tangier gate status --json`` once every job had ended, with the
``"type": "status"`` that only ``--watch`` prints added by hand.
"""

import json
from pathlib import Path

from mael_domain.external_event import gate_event
from mael_orchestrator.gate_table import GateTable

FIXTURES = Path(__file__).parent / "fixtures"
LINES = [
    json.loads(line)
    for line in (FIXTURES / "gate-watch.jsonl").read_text().splitlines()
]
AFTER = json.loads((FIXTURES / "gate-status-after.json").read_text())


def _table(table: GateTable) -> dict:
    return {
        job_id: (job["state"], {g["name"]: g["state"] for g in job["gates"]})
        for job_id, job in table.jobs.items()
    }


def _replay(lines: list[dict]) -> tuple[GateTable, list[tuple[dict, list]]]:
    table = GateTable()
    trace = []
    for line in lines:
        ended = table.apply(line)
        trace.append((_table(table), [(e.job["id"], e.job["state"]) for e in ended]))
    return table, trace


def test_the_table_follows_each_line():
    _, trace = _replay(LINES)
    pending_1100 = {"ok": "pending", "bad": "pending"}
    assert trace == [
        ({}, []),
        ({1100: ("queued", pending_1100)}, []),
        ({1100: ("running", pending_1100)}, []),
        ({1100: ("running", {"ok": "running", "bad": "pending"})}, []),
        ({1100: ("running", {"ok": "passed", "bad": "pending"})}, []),
        ({1100: ("running", {"ok": "passed", "bad": "running"})}, []),
        ({1100: ("running", {"ok": "passed", "bad": "failed"})}, []),
        ({}, [(1100, "failed")]),
        ({1107: ("queued", {"ok": "pending"})}, []),
        ({1107: ("running", {"ok": "pending"})}, []),
        ({1107: ("running", {"ok": "verified"})}, []),
        ({}, [(1107, "passed")]),
        ({1112: ("queued", {"slow": "pending"})}, []),
        # The restarted watch: the job is still queued, so nothing ended.
        ({1112: ("queued", {"slow": "pending"})}, []),
        ({1112: ("queued", {"slow": "cancelled"})}, []),
        ({}, [(1112, "cancelled")]),
    ]


def test_each_recorded_end_is_its_event():
    table = GateTable()
    events = [gate_event(ended.job) for line in LINES for ended in table.apply(line)]
    assert [(e.type, e.attrs) for e in events] == [
        ("gate.failed", {"job": "1100", "head": "0c72dc6"}),
        ("gate.passed", {"job": "1107", "head": "0c72dc6"}),
        ("gate.cancelled", {"job": "1112", "head": "0c72dc6"}),
    ]


def test_a_restart_snapshot_ends_a_job_the_table_held():
    """The watch died while job 1112 was queued, and the job ended before it came back."""
    table, _ = _replay(LINES[:13])
    ended = table.apply(AFTER)
    assert [(e.job["id"], e.job["state"]) for e in ended] == [(1112, "cancelled")]
    assert table.jobs == {}


def test_a_snapshot_ends_only_the_jobs_the_table_held():
    """A first watch reads ``recent`` as history, not news."""
    assert GateTable().apply(AFTER) == []


def test_a_job_that_is_new_and_already_finished_ended():
    """A short job can start and end between two of tangier's reads."""
    assert [e.job["id"] for e in GateTable().apply(LINES[11])] == [1107]


def test_a_gate_line_for_a_job_the_table_does_not_hold_is_ignored():
    """A finished job's gate lines come before its job line."""
    table = GateTable()
    assert table.apply(LINES[10]) == []
    assert table.jobs == {}


def test_a_gate_line_adds_a_gate_the_job_did_not_list():
    table, _ = _replay(LINES[:3])
    new_gate = {**LINES[3], "gate": {**LINES[3]["gate"], "name": "extra"}}
    table.apply(new_gate)
    assert _table(table) == {
        1100: ("running", {"ok": "pending", "bad": "pending", "extra": "running"})
    }
