"""The server's door onto tangier's gate watch, against a stand-in ``tangier``."""

import asyncio
import json
import os
from contextlib import aclosing

import pytest

from mael_orchestrator.gate_source import GateSourceError, watch_gates

STATUS = {"type": "status", "running": [], "queued": [], "recent": []}
JOB = {"type": "job", "previous": None, "job": {"id": 7, "state": "queued"}}


def _fake_tangier(tmp_path, monkeypatch, lines: list[str], then: str = "exit 0"):
    """Put a stand-in ``tangier`` first on PATH that prints ``lines``, then runs ``then``.

    Returns the file that logs each call's arguments, and the file the
    stand-in writes its pid to.
    """
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "tangier-args.log"
    pid = tmp_path / "tangier.pid"
    output = "".join(f"{line}\n" for line in lines)
    script = bin_dir / "tangier"
    script.write_text(
        f'#!/bin/sh\necho "$*" >> {log}\necho $$ > {pid}\n'
        f"cat <<'OUT'\n{output}OUT\n{then}\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return log, pid


async def _read(n: int) -> list[dict]:
    seen = []
    async with aclosing(watch_gates()) as events:
        async for event in events:
            seen.append(event)
            if len(seen) == n:
                break
    return seen


async def test_each_line_is_one_event(tmp_path, monkeypatch):
    log, _ = _fake_tangier(
        tmp_path,
        monkeypatch,
        [json.dumps(STATUS), json.dumps(JOB)],
        then="exec sleep 30",
    )
    assert await _read(2) == [STATUS, JOB]
    assert log.read_text() == "gate status --watch --json --all\n"


async def test_a_line_longer_than_the_stream_default_is_one_event(
    tmp_path, monkeypatch
):
    """The first ``--all`` status lists the machine's recent jobs: 530 KB on a busy day."""
    big = {**STATUS, "recent": [{"id": n, "state": "passed"} for n in range(40_000)]}
    _fake_tangier(tmp_path, monkeypatch, [json.dumps(big)], then="exec sleep 30")
    assert await _read(1) == [big]


async def test_a_watch_that_ends_is_an_error(tmp_path, monkeypatch):
    """With the tail of stderr, so an old tangier's refusal is visible."""
    _fake_tangier(
        tmp_path,
        monkeypatch,
        [json.dumps(STATUS)],
        then="echo 'error: unrecognized arguments: --watch' >&2; exit 2",
    )
    seen = []
    with pytest.raises(GateSourceError, match="exit 2.*unrecognized arguments"):
        async for event in watch_gates():
            seen.append(event)
    assert seen == [STATUS]


@pytest.mark.parametrize("line", ["Error: cannot write lock", "[1, 2]"])
async def test_a_line_that_is_not_a_json_object_is_an_error(
    tmp_path, monkeypatch, line
):
    _fake_tangier(tmp_path, monkeypatch, [line], then="exec sleep 30")
    with pytest.raises(GateSourceError, match="not a JSON object"):
        await _read(1)


async def test_no_tangier_on_path_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(GateSourceError, match="not on PATH"):
        await _read(1)


async def test_a_cancelled_watch_kills_tangier(tmp_path, monkeypatch):
    _, pid_file = _fake_tangier(
        tmp_path, monkeypatch, [json.dumps(STATUS)], then="exec sleep 30"
    )
    first = asyncio.Event()

    async def reader():
        async with aclosing(watch_gates()) as events:
            async for _ in events:
                first.set()

    task = asyncio.create_task(reader())
    # The stand-in writes its pid before it prints.
    await asyncio.wait_for(first.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
