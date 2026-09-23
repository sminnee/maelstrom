"""The ``claude`` processes on the machine, read from a faked process table."""

import asyncio
import subprocess

import pytest

from mael_common import process_table


def _async_returning(fn):
    """Wrap a synchronous stub so it can stand in for ``run_cmd_async``."""

    async def run(cmd, *args, **kwargs):
        return fn(cmd, **kwargs)

    return run


class TestProcessTable:
    DRIVEN = (
        "claude -p --input-format stream-json --output-format stream-json "
        '--verbose --permission-prompt-tool stdio --settings {"a": 1} '
        "--resume 0f8fad5b-d9cb-469f-a165-70867728950e"
    )

    def test_session_id_in_reads_both_flags(self):
        sid = "0f8fad5b-d9cb-469f-a165-70867728950e"
        assert process_table.session_id_in(f"claude --session-id {sid}") == sid
        assert process_table.session_id_in(f"claude --resume {sid}") == sid
        assert process_table.session_id_in("claude --resume") is None

    def test_is_driven_needs_both_daemon_flags(self):
        assert process_table.is_driven(self.DRIVEN)
        assert not process_table.is_driven("claude --session-id x")
        assert not process_table.is_driven(
            "claude -p --input-format stream-json --output-format stream-json"
        )

    def test_parse_process_table_reads_pid_pgid_and_the_whole_command(self):
        rows = process_table.parse_process_table(
            f"  101   101 {self.DRIVEN}\n  202    50 claude\ngarbage line\n"
        )
        assert rows == [
            process_table.ProcessInfo(101, 101, self.DRIVEN),
            process_table.ProcessInfo(202, 50, "claude"),
        ]

    def test_list_unions_both_pgrep_sweeps_and_reads_ps_at_full_width(
        self, monkeypatch
    ):
        calls: list[list[str]] = []

        async def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            if cmd[0] == "pgrep" and cmd[1] == "-x":
                return subprocess.CompletedProcess(cmd, 0, stdout="101\n", stderr="")
            if cmd[0] == "pgrep":
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="101\n202\n", stderr=""
                )
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=f"101 101 {self.DRIVEN}\n202 202 node claude\n",
                stderr="",
            )

        monkeypatch.setattr(process_table, "run_cmd_async", fake_run)
        rows = asyncio.run(process_table.list_claude_processes())
        assert [r.pid for r in rows] == [101, 202]
        ps = calls[-1]
        assert ps[:2] == ["ps", "-ww"]
        assert sorted(ps[-1].split(",")) == ["101", "202"]

    def test_no_match_is_an_empty_table_not_an_error(self, monkeypatch):
        monkeypatch.setattr(
            process_table,
            "run_cmd_async",
            _async_returning(
                lambda cmd, **kw: subprocess.CompletedProcess(
                    cmd, 1, stdout="", stderr=""
                )
            ),
        )
        assert asyncio.run(process_table.list_claude_processes()) == []

    def test_a_closed_process_table_raises_rather_than_reading_as_empty(
        self, monkeypatch
    ):
        """Inside an agent sandbox `pgrep` exits 3 and `ps` will not run.

        Read as "no processes", a reconcile would write every record off as
        crashed. So the reader says it could not look.
        """
        monkeypatch.setattr(
            process_table,
            "run_cmd_async",
            _async_returning(
                lambda cmd, **kw: subprocess.CompletedProcess(
                    cmd, 3, stdout="", stderr="pgrep: Cannot get process list"
                )
            ),
        )
        with pytest.raises(process_table.ProcessTableUnavailable):
            asyncio.run(process_table.list_claude_processes())

        async def ps_refused(cmd, **kw):
            if cmd[0] == "pgrep":
                return subprocess.CompletedProcess(cmd, 0, stdout="101\n", stderr="")
            raise OSError("operation not permitted: ps")

        monkeypatch.setattr(process_table, "run_cmd_async", ps_refused)
        with pytest.raises(process_table.ProcessTableUnavailable):
            asyncio.run(process_table.list_claude_processes())

    def test_ps_exit_1_means_some_pids_have_gone_not_a_closed_table(self, monkeypatch):
        async def some_gone(cmd, **kw):
            if cmd[0] == "pgrep":
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="101\n202\n", stderr=""
                )
            return subprocess.CompletedProcess(
                cmd, 1, stdout=f"101 101 {self.DRIVEN}\n", stderr=""
            )

        monkeypatch.setattr(process_table, "run_cmd_async", some_gone)
        assert [r.pid for r in asyncio.run(process_table.list_claude_processes())] == [
            101
        ]


def test_cwds_for_pids_pairs_each_pid_with_its_cwd(monkeypatch):
    async def lsof(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 1, stdout="p101\nn/w/alpha\np202\np303\nn/w/bravo\n", stderr=""
        )

    monkeypatch.setattr(process_table, "run_cmd_async", lsof)
    cwds = asyncio.run(process_table.cwds_for_pids([101, 202, 303]))
    assert {pid: str(cwd) for pid, cwd in cwds.items()} == {
        101: "/w/alpha",
        303: "/w/bravo",
    }


def test_command_of_reads_one_process_at_full_width(monkeypatch):
    calls = []

    async def ps(cmd, **kw):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(
            cmd, 0, stdout="/opt/homebrew/bin/claude --resume x\n", stderr=""
        )

    monkeypatch.setattr(process_table, "run_cmd_async", ps)
    assert asyncio.run(process_table.command_of(42)) == (
        "/opt/homebrew/bin/claude --resume x"
    )
    assert calls == [["ps", "-ww", "-o", "command=", "-p", "42"]]


def test_command_of_a_gone_pid_or_a_missing_ps_is_none(monkeypatch):
    async def gone(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr(process_table, "run_cmd_async", gone)
    assert asyncio.run(process_table.command_of(42)) is None

    async def missing(cmd, **kw):
        raise OSError("ps not found")

    monkeypatch.setattr(process_table, "run_cmd_async", missing)
    assert asyncio.run(process_table.command_of(42)) is None


def test_a_pgrep_line_that_is_not_a_pid_is_skipped(monkeypatch):
    async def run(cmd, **kw):
        if cmd[0] == "pgrep":
            return subprocess.CompletedProcess(
                cmd, 0, stdout="garbage\n42\n", stderr=""
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="42 42 claude\n", stderr="")

    monkeypatch.setattr(process_table, "run_cmd_async", run)
    rows = asyncio.run(process_table.list_claude_processes())
    assert [r.pid for r in rows] == [42]
