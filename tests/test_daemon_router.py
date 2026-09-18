"""Harness routing at the orchestrator daemon-client seam."""

import asyncio
from dataclasses import dataclass, field

from maelstrom.orchestrator.daemon_bridge import DaemonRouter, ScriptedAsyncDaemonClient


@dataclass
class Agents:
    rows: dict[str, dict] = field(default_factory=dict)
    removed: list[str] = field(default_factory=list)

    async def save(self, agent: dict) -> None:
        self.rows[agent["id"]] = agent

    async def list(self) -> list[dict]:
        return list(self.rows.values())

    async def remove(self, agent_id: str) -> None:
        self.removed.append(agent_id)
        self.rows.pop(agent_id, None)


def test_router_stores_every_started_agent_with_its_harness_and_mode() -> None:
    async def scenario():
        claude = ScriptedAsyncDaemonClient()
        codex = ScriptedAsyncDaemonClient(next_start_id="thread-1")
        agents = Agents()
        router = DaemonRouter(claude, codex, agents)
        codex_reply = await router.request(
            {
                "cmd": "start",
                "cwd": "/worktree",
                "model": "codex:sol",
                "mode": "plan",
                "session": "task-session-1",
                "env": {"MAEL_TASK_ID": "2026-09-16.4.3"},
            }
        )
        codex_row = dict(agents.rows["thread-1"])
        claude_reply = await router.request(
            {
                "cmd": "start",
                "cwd": "/other-worktree",
                "model": "claude:opus",
                "mode": "auto",
            }
        )
        stopped = await router.request({"cmd": "stop", "id": "thread-1"})
        return (
            codex_reply,
            codex_row,
            claude_reply,
            stopped,
            claude.calls,
            codex.calls,
            agents.rows,
            agents.removed,
        )

    (
        codex_reply,
        codex_row,
        claude_reply,
        stopped,
        claude_calls,
        codex_calls,
        rows,
        removed,
    ) = asyncio.run(scenario())

    assert codex_reply == {"ok": True, "id": "thread-1"}
    assert codex_row == {
        "id": "thread-1",
        "harness": "codex",
        "task_session_id": "task-session-1",
        "task_id": "2026-09-16.4.3",
        "cwd": "/worktree",
        "model": "codex:sol",
        "mode": "plan",
    }
    assert claude_reply == {"ok": True, "id": "new1"}
    assert stopped == {"ok": True}
    assert claude_calls[0]["cmd"] == "start"
    assert codex_calls[0]["cmd"] == "start"
    assert rows == {
        "new1": {
            "id": "new1",
            "harness": "claude",
            "task_session_id": "",
            "task_id": "",
            "cwd": "/other-worktree",
            "model": "claude:opus",
            "mode": "auto",
        }
    }
    assert removed == ["thread-1"]


def test_list_passes_through_a_live_subagent_of_a_stored_agent() -> None:
    async def scenario():
        claude = ScriptedAsyncDaemonClient()
        codex = ScriptedAsyncDaemonClient()
        agents = Agents(
            rows={
                "ag1": {
                    "id": "ag1",
                    "harness": "claude",
                    "task_session_id": "task-session-1",
                    "cwd": "/worktree",
                    "model": "claude:opus",
                    "mode": "normal",
                }
            }
        )
        claude.rows["ag1"] = {
            "id": "ag1",
            "state": "idle",
            "session": "task-session-1",
            "cwd": "/worktree",
            "model": "claude:opus",
            "parent": "",
        }
        claude.rows["ag1.1"] = {
            "id": "ag1.1",
            "state": "idle",
            "session": "",
            "cwd": "/worktree",
            "model": "claude:opus",
            "parent": "ag1",
        }
        router = DaemonRouter(claude, codex, agents)
        return await router.request({"cmd": "list"})

    listed = asyncio.run(scenario())

    ids = {row["id"] for row in listed["agents"]}
    assert ids == {"ag1", "ag1.1"}
    child = next(row for row in listed["agents"] if row["id"] == "ag1.1")
    assert child["parent"] == "ag1"


def test_list_reports_a_stored_agent_absent_from_the_daemon_as_exited() -> None:
    """A stored id must disappear from ``state`` too, not just from the daemon.

    The server's reconcile loop only exits an agent whose id drops out of
    ``list`` entirely. A stored agent's id never drops out on its own, so a
    missing live row has to read as ``exited`` or the loop can never retire it.
    """

    async def scenario():
        agents = Agents(
            rows={
                "ag1": {
                    "id": "ag1",
                    "harness": "claude",
                    "task_session_id": "task-session-1",
                    "cwd": "/worktree",
                    "model": "claude:opus",
                    "mode": "normal",
                }
            }
        )
        router = DaemonRouter(
            ScriptedAsyncDaemonClient(), ScriptedAsyncDaemonClient(), agents
        )
        return await router.request({"cmd": "list"})

    listed = asyncio.run(scenario())

    assert listed["agents"] == [
        {
            "id": "ag1",
            "state": "exited",
            "session": "task-session-1",
            "cwd": "/worktree",
            "model": "claude:opus",
            "mode": "normal",
        }
    ]


def test_set_mode_updates_the_stored_record() -> None:
    async def scenario():
        claude = ScriptedAsyncDaemonClient(next_start_id="a1")
        agents = Agents()
        router = DaemonRouter(claude, ScriptedAsyncDaemonClient(), agents)
        await router.request(
            {
                "cmd": "start",
                "cwd": "/worktree",
                "model": "claude:opus",
                "mode": "normal",
            }
        )
        reply = await router.request({"cmd": "set-mode", "id": "a1", "mode": "auto"})
        return reply, agents.rows

    reply, rows = asyncio.run(scenario())

    assert reply == {"ok": True}
    assert rows["a1"]["mode"] == "auto"


def test_router_refuses_opencode_daemon_launch() -> None:
    async def scenario():
        router = DaemonRouter(
            ScriptedAsyncDaemonClient(), ScriptedAsyncDaemonClient(), Agents()
        )
        return await router.request(
            {"cmd": "start", "cwd": "/worktree", "model": "opencode:glm"}
        )

    assert asyncio.run(scenario()) == {
        "ok": False,
        "error": "The opencode daemon is not available.",
    }
