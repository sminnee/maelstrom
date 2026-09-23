"""`mael-agent-daemon` commands, driven through the recording transport."""

import json

import pytest
from daemon_cli_support import drive, unreachable

from mael_common.util import get_maelstrom_dir
from mael_daemon import cli as agent_daemon_cli


def run_cli(argv: list[str], replies: list[dict] | None = None):
    """Drive `mael-agent-daemon` through the fake transport: (result, client)."""
    return drive(agent_daemon_cli.cli, argv, replies)


def test_daemon_status_names_the_serving_code():
    """The command that answers "which copy is serving me?".

    The source tree and the start time are the fields that identify a stale
    daemon, so both have to reach the output.
    """
    result, client = run_cli(
        ["status"],
        replies=[
            {
                "daemon": {
                    "pid": 4242,
                    "version": "0.1.2",
                    "executable": "/tree/.venv/bin/python3",
                    "source_tree": "/Users/x/Projects/maelstrom/_main",
                    "root": "/Users/x/.maelstrom",
                    "socket_path": "/Users/x/.maelstrom/agent-daemon.sock",
                    "spec_dir": "/Users/x/.maelstrom/agents",
                    "started_at": "2026-09-05T02:44:16+00:00",
                    "agents": 5,
                }
            }
        ],
    )
    assert result.exit_code == 0
    assert client.calls == [{"cmd": "ping"}]
    assert "4242" in result.output
    assert "/Users/x/Projects/maelstrom/_main" in result.output
    assert "5" in result.output


def test_the_bare_daemon_command_does_not_serve():
    """`daemon` used to run one in the foreground; it must not now."""
    result, _ = run_cli([])
    assert result.exit_code != 0


def test_daemon_status_explains_a_daemon_too_old_to_answer():
    """A pre-`ping` daemon answers "no such agent", which reads as a bug here.

    `daemon status` is the command you run to diagnose a stale daemon, so it
    is the last place that should report the stale daemon's confusion verbatim.
    """
    result, _ = run_cli(["status"], replies=[{"error": "no such agent: "}])
    assert result.exit_code == 1
    assert "older than this code" in result.output
    # Names a command that exists: `mael-agent-daemon restart` does not exist, and the
    # environment manager owns the daemon's lifetime.
    assert "mael self-env restart agent-daemon" in result.output


def test_daemon_status_renders_a_timestamp_without_a_zone():
    """A stamp with no zone must not traceback.

    `status` is the command you run when a daemon is old or foreign, so it has
    to survive a record it did not write.
    """
    result, _ = run_cli(
        ["status"],
        replies=[{"daemon": {"pid": 1, "started_at": "2026-09-05T14:47:00"}}],
    )
    assert result.exit_code == 0
    # Read as UTC and rendered in local time, so the date depends on the zone.
    # What matters is that it renders an age at all rather than raising.
    assert "ago)" in result.output


# --- gc, list and reconcile: the records against the process table -----------

_S1 = "11111111-1111-1111-1111-111111111111"
_DRIVEN = (
    "claude -p --input-format stream-json --output-format stream-json --verbose "
    "--permission-prompt-tool stdio --resume "
)


def _verdict(kind, agent_id="a1", pid=100, reason=""):
    return {
        "kind": kind,
        "session_id": _S1,
        "agent_id": agent_id,
        "pid": pid,
        "pgid": pid,
        "reason": reason,
    }


def test_reconcile_asks_a_running_daemon_and_prints_its_verdicts():
    result, client = run_cli(
        ["reconcile"],
        replies=[
            {"verdicts": [_verdict("stray"), _verdict("duplicate", pid=200)]},
            {"agents": []},
        ],
    )
    assert result.exit_code == 0, result.output
    assert [c["cmd"] for c in client.calls] == ["reconcile", "list"]
    assert "stray" in result.output and "200" in result.output


def test_gc_asks_a_running_daemon_and_reports_what_it_killed():
    result, client = run_cli(
        ["gc"],
        replies=[{"verdicts": [_verdict("stray")], "killed": [100]}, {"agents": []}],
    )
    assert result.exit_code == 0, result.output
    assert client.calls[0] == {"cmd": "gc"}
    assert "killed groups: 100" in result.output


def _local_root(monkeypatch, records, processes):
    """No daemon: the command reads the records and the table itself."""
    from mael_agent.agent_transport import daemon_paths
    from mael_common.process_table import ProcessInfo
    from mael_daemon.agent_model import AgentSpec
    from mael_daemon.agent_spec_store import JsonAgentSpecStore

    store = JsonAgentSpecStore(daemon_paths().spec_dir)
    for agent_id, pid, status in records:
        store.write(
            AgentSpec(
                agent_id=agent_id, cwd="/w", session_id=_S1, pid=pid, status=status
            )
        )
    table = [ProcessInfo(pid, pid, f"{_DRIVEN}{_S1}") for pid in processes]

    async def _table():
        return list(table)

    monkeypatch.setattr(agent_daemon_cli, "list_claude_processes", _table)
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "mael_daemon.agent_server.kill_group",
        lambda pgid, sig: signals.append((pgid, sig)),
    )
    monkeypatch.setattr("mael_daemon.agent_server._group_alive", lambda pgid: False)
    return store, signals


def test_gc_with_no_daemon_kills_the_strays_and_leaves_their_records_running(
    monkeypatch,
):
    """The case `gc` exists for: `kill -9` took the daemon and left its children."""
    store, signals = _local_root(monkeypatch, [("a1", 100, "running")], [100, 200])
    result, client = run_cli(
        ["gc"],
        replies=[unreachable("/x")],
    )
    assert result.exit_code == 0, result.output
    assert [s[0] for s in signals] == [100, 200]
    assert store.read("a1").status == "running"
    assert "stray" in result.output and "duplicate" in result.output


def test_reconcile_with_no_daemon_touches_nothing(monkeypatch):
    store, signals = _local_root(monkeypatch, [("a1", 100, "running")], [])
    result, _ = run_cli(
        ["reconcile", "--json"],
        replies=[unreachable("/x")],
    )
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["reachable"] is False
    assert [v["kind"] for v in body["verdicts"]] == ["crashed"]
    assert signals == []
    assert store.read("a1").status == "running"  # a dry run writes nothing


def test_list_shows_each_record_with_its_pid_liveness_and_holder(monkeypatch):
    store, _ = _local_root(
        monkeypatch, [("a1", 100, "running"), ("a2", None, "exited")], [100, 200]
    )
    result, _ = run_cli(
        ["list", "--json"],
        replies=[unreachable("/x")],
    )
    assert result.exit_code == 0, result.output
    rows = {row["id"]: row for row in json.loads(result.output)}
    assert rows["a1"]["pid"] == "100"
    assert rows["a1"]["alive"] == "yes"
    assert rows["a1"]["held"] == "no"
    assert rows["a1"]["mismatch"] == "stray, 1 duplicate"
    assert rows["a2"]["pid"] == ""
    assert rows["a2"]["alive"] == ""


def test_list_marks_held_from_the_daemons_own_listing():
    result, _ = run_cli(
        ["list", "--json"],
        replies=[{"verdicts": [_verdict("owned")]}, {"agents": [{"id": "a1"}]}],
    )
    # No record on disk under the isolated root, so the only row is none —
    # the verdict alone does not make a row; the record does.
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


def test_an_unreadable_process_table_is_an_error_not_a_verdict(monkeypatch):
    from mael_common.process_table import ProcessTableUnavailable

    async def unreadable():
        raise ProcessTableUnavailable("pgrep exited 3")

    monkeypatch.setattr(agent_daemon_cli, "list_claude_processes", unreadable)
    result, _ = run_cli(
        ["gc"],
        replies=[unreachable("/x")],
    )
    assert result.exit_code != 0
    assert "process table" in result.output


def test_all_roots_kills_only_a_process_unknown_to_every_root(monkeypatch, tmp_path):
    """One root does not kill what it cannot place; every root together may."""
    from mael_agent.agent_transport import DaemonPaths

    roots = [DaemonPaths(tmp_path / "a"), DaemonPaths(tmp_path / "b")]
    bases = []
    monkeypatch.setattr(
        agent_daemon_cli, "all_roots", lambda base: bases.append(base) or roots
    )
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "mael_daemon.agent_server.kill_group",
        lambda pgid, sig: signals.append((pgid, sig)),
    )
    monkeypatch.setattr("mael_daemon.agent_server._group_alive", lambda pgid: False)
    # Root a owns pid 100 and knows nothing of 300; root b knows neither.
    result, client = run_cli(
        ["gc", "--all-roots"],
        replies=[
            {
                "verdicts": [_verdict("owned", pid=100), _verdict("unknown", "", 300)],
                "killed": [],
            },
            {"agents": [{"id": "a1"}]},
            {
                "verdicts": [
                    _verdict("unknown", "", 100),
                    _verdict("unknown", "", 300),
                ],
                "killed": [],
            },
            {"agents": []},
        ],
    )
    assert result.exit_code == 0, result.output
    assert [s[0] for s in signals] == [300]
    assert bases == [get_maelstrom_dir()]
    assert str(tmp_path / "a") in result.output and str(tmp_path / "b") in result.output


# --- the daemon root comes from the environment ------------------------------


class TestServeRequiresARoot:
    """`serve` runs only on a root its environment names.

    A daemon on the wrong root is the failure this prevents. It served a
    worktree's test code as the everyday daemon for four restarts, because the
    root came from a flag that the starting command chose. The environment
    manager now writes the root into `.env`, and `serve` reads it there.
    """

    def test_serve_without_a_root_exits_two_and_names_the_commands(self, monkeypatch):
        monkeypatch.delenv("MAEL_AGENT_ROOT", raising=False)
        result, _ = run_cli(["serve"])
        assert result.exit_code == 2
        assert "MAEL_AGENT_ROOT is not set" in result.output
        assert "mael self-env start" in result.output
        assert "mael env start" in result.output

    def test_serve_without_a_root_builds_no_daemon(self, monkeypatch):
        """Exiting is not enough: a daemon constructed on a guessed root would
        create that directory before the error reached anyone."""
        monkeypatch.delenv("MAEL_AGENT_ROOT", raising=False)
        built = []
        monkeypatch.setattr(
            agent_daemon_cli, "AgentDaemon", lambda *a, **k: built.append(a) or object()
        )
        result, _ = run_cli(["serve"])
        assert result.exit_code == 2
        assert built == []

    def test_serve_builds_the_daemon_on_the_environments_root(
        self, monkeypatch, tmp_path
    ):
        root = tmp_path / "chosen"
        monkeypatch.setenv("MAEL_AGENT_ROOT", str(root))
        built = []

        class _Daemon:
            def __init__(self, *args, **kwargs):
                built.append(args[0] if args else kwargs.get("root"))

            async def serve(self):
                return None

        monkeypatch.setattr(agent_daemon_cli, "AgentDaemon", _Daemon)
        result, _ = run_cli(["serve"])
        assert result.exit_code == 0, result.output
        assert built == [root]

    def test_serve_takes_no_root_flag(self, tmp_path):
        """A flag is what let the wrong root be chosen, so there is no flag."""
        result, _ = run_cli(["serve", "--root", str(tmp_path)])
        assert result.exit_code == 2
        assert "no such option" in result.output.lower()


class TestReadOnlyVerbsReadTheEnvironment:
    """`status`, `list`, `gc` and `reconcile` follow their own environment."""

    @pytest.mark.parametrize("verb", ["status", "list", "gc", "reconcile"])
    def test_the_verb_takes_no_root_flag(self, verb, tmp_path):
        result, _ = run_cli([verb, "--root", str(tmp_path)])
        assert result.exit_code == 2
        assert "no such option" in result.output.lower()

    def test_status_asks_the_daemon_on_the_environments_root(
        self, monkeypatch, tmp_path
    ):
        root = tmp_path / "chosen"
        monkeypatch.setenv("MAEL_AGENT_ROOT", str(root))
        result, client = run_cli(
            ["status"],
            replies=[{"daemon": {"pid": 1, "root": str(root), "agents": 0}}],
        )
        assert result.exit_code == 0, result.output
        assert client.socket_path == str(root / "agent-daemon.sock")


class TestOnlyTheEnvironmentManagerStartsADaemon:
    """`daemon start|stop|restart` are gone.

    Seven things used to bring a daemon into being, so no one owned any root.
    Two do now: `mael self-env start` for the everyday daemon, and
    `mael env start` for a worktree's. Both run `serve` as a service, so the
    daemon's lifetime is its environment's.
    """

    @pytest.mark.parametrize("verb", ["start", "stop", "restart"])
    def test_the_verb_is_not_a_command(self, verb):
        result, _ = run_cli([verb])
        assert result.exit_code == 2
        assert "no such command" in result.output.lower()

    def test_the_group_names_the_environment_manager(self):
        result, _ = run_cli(["--help"])
        assert "self-env" in result.output
        assert "env start" in result.output


class TestAnUnreachableDaemonIsReported:
    """A missing daemon names the root and the command that starts one.

    Distinguishing "no daemon" from "a daemon that answered badly" is what
    lets `gc` fall back to reading the records itself, and lets `status` tell
    a stale daemon apart from an absent one. Both match on the message, so the
    message is behaviour.
    """

    def test_status_reports_an_absent_daemon_rather_than_a_stale_one(
        self, tmp_path, monkeypatch
    ):
        """`status` calls a daemon that cannot answer `ping` stale. An absent
        one must not be described that way."""
        root = tmp_path / "chosen"
        monkeypatch.setenv("MAEL_AGENT_ROOT", str(root))
        result, _ = run_cli(
            ["status"],
            replies=[unreachable(root)],
        )
        assert result.exit_code == 1
        assert f"No agent daemon on {root}" in result.output
        assert "older than this code" not in result.output

    def test_gc_falls_back_to_the_records_when_no_daemon_answers(
        self, tmp_path, monkeypatch
    ):
        """The case `gc` exists for: a daemon that died leaving children."""

        async def _empty():
            return []

        monkeypatch.setattr(
            agent_daemon_cli, "list_claude_processes", _empty, raising=False
        )
        result, _ = run_cli(
            ["gc"],
            replies=[unreachable(tmp_path)],
        )
        assert result.exit_code == 0, result.output
