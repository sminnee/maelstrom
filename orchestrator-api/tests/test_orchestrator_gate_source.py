"""The server's door onto tangier's gate jobs, against a stand-in ``tangier``."""

import json
import os

import pytest

from mael_orchestrator.gate_source import GateSourceError, gate_jobs

JOBS = """[
  {"id": 899, "state": "queued", "queue_position": 1,
   "worktree": "/p/northwind-alpha", "head": "a2fc5ee2", "dirty": false,
   "started": "2026-10-21T02:03:25+00:00",
   "gates": [{"name": "lint", "state": "pending"}]}
]
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


async def test_the_jobs_are_the_parsed_running_list(tmp_path, monkeypatch):
    log = _fake_tangier(tmp_path, monkeypatch, JOBS)
    assert await gate_jobs() == json.loads(JOBS)
    assert log.read_text() == "gate status --all --running --json\n"


async def test_a_failed_call_is_an_error(tmp_path, monkeypatch):
    """Not "no jobs": that would clear the jobs the dashboard shows."""
    _fake_tangier(tmp_path, monkeypatch, "boom\n", exit_code=2)
    with pytest.raises(GateSourceError, match="exit 2"):
        await gate_jobs()


async def test_output_that_is_not_json_is_an_error(tmp_path, monkeypatch):
    _fake_tangier(tmp_path, monkeypatch, "Error: cannot write lock\n")
    with pytest.raises(GateSourceError, match="not JSON"):
        await gate_jobs()


@pytest.mark.parametrize("output", ['{"error": "no jobs dir"}\n', "[1, 2]\n"])
async def test_json_that_is_not_a_list_of_jobs_is_an_error(
    tmp_path, monkeypatch, output
):
    _fake_tangier(tmp_path, monkeypatch, output)
    with pytest.raises(GateSourceError, match="no JSON list of jobs"):
        await gate_jobs()


async def test_no_tangier_on_path_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(GateSourceError, match="not on PATH"):
        await gate_jobs()
