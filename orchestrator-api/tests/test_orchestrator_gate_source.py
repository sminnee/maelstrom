"""The server's door onto tangier's gate jobs, against a stand-in ``tangier``."""

import json
import os

import pytest

from mael_orchestrator.gate_source import GateSourceError, gate_jobs

STATUS = """{
  "machine": {"slots_in_use": 1, "max_running": 4, "queued": 1, "load": 3.2,
              "cpus": 8, "memory_total_bytes": null,
              "jobs_cpu_percent": 94.0, "jobs_rss_bytes": 112197632},
  "running": [
    {"id": 898, "state": "running", "queue_position": null,
     "worktree": "/p/northwind-alpha", "head": "a2fc5ee2", "dirty": false,
     "started": "2026-10-21T02:03:20+00:00",
     "usage": {"cpu_percent": 94.0, "rss_bytes": 112197632, "processes": 3},
     "gates": [{"name": "test", "state": "running"}]}
  ],
  "queued": [
    {"id": 899, "state": "queued", "queue_position": 1,
     "worktree": "/p/northwind-alpha", "head": "a2fc5ee2", "dirty": false,
     "started": "2026-10-21T02:03:25+00:00", "usage": null,
     "gates": [{"name": "lint", "state": "pending"}]}
  ],
  "recent": [
    {"id": 897, "state": "passed", "queue_position": null,
     "worktree": "/p/northwind-alpha", "head": "a2fc5ee2", "dirty": false,
     "started": "2026-10-21T01:00:00+00:00", "usage": null, "gates": []}
  ],
  "not_found": []
}
"""


def _fake_tangier(tmp_path, monkeypatch, output: str, exit_code: int = 0):
    """Put a stand-in ``tangier`` first on PATH that prints ``output``.

    Returns the file that logs each call's arguments.
    """
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "tangier-args.log"
    script = bin_dir / "tangier"
    script.write_text(
        f"#!/bin/sh\necho \"$*\" >> {log}\ncat <<'OUT'\n{output}OUT\nexit {exit_code}\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return log


async def test_the_jobs_are_the_running_then_the_queued(tmp_path, monkeypatch):
    """Not ``recent``: the finished jobs are not on the list."""
    log = _fake_tangier(tmp_path, monkeypatch, STATUS)
    status = json.loads(STATUS)
    assert await gate_jobs() == status["running"] + status["queued"]
    assert log.read_text() == "gate status --all --running --json\n"


async def test_an_idle_machine_has_no_jobs(tmp_path, monkeypatch):
    _fake_tangier(
        tmp_path, monkeypatch, '{"running": [], "queued": [], "recent": []}\n'
    )
    assert await gate_jobs() == []


async def test_a_failed_call_is_an_error(tmp_path, monkeypatch):
    """Not "no jobs": that would clear the jobs the dashboard shows."""
    _fake_tangier(tmp_path, monkeypatch, "boom\n", exit_code=2)
    with pytest.raises(GateSourceError, match="exit 2"):
        await gate_jobs()


async def test_output_that_is_not_json_is_an_error(tmp_path, monkeypatch):
    _fake_tangier(tmp_path, monkeypatch, "Error: cannot write lock\n")
    with pytest.raises(GateSourceError, match="not JSON"):
        await gate_jobs()


@pytest.mark.parametrize(
    "output",
    [
        '{"error": "no jobs dir"}\n',
        '{"running": [1, 2], "queued": []}\n',
        # tangier before 0.3.0 printed a flat list.
        '[{"id": 1}]\n',
    ],
)
async def test_json_that_is_not_a_status_object_is_an_error(
    tmp_path, monkeypatch, output
):
    _fake_tangier(tmp_path, monkeypatch, output)
    with pytest.raises(GateSourceError, match="no gate status object"):
        await gate_jobs()


async def test_no_tangier_on_path_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(GateSourceError, match="not on PATH"):
        await gate_jobs()
