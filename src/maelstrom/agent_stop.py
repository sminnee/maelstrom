"""Stop the daemon's agents in a worktree, before the worktree goes away.

``mael close`` tears a worktree down. A driven agent runs there as a ``claude``
process, so the pid sweep in ``env.stop_sessions`` finds and signals it. The
daemon sees that as an unexpected exit and records the agent ``exited``, which
is how it records a crash — so every closed worktree would leave a phantom
crashed agent in ``mael agent list --all`` and in the orchestrator UI.

Asking the daemon to stop them first records a deliberate stop instead. The pid
sweep still runs after, and still catches a session the daemon does not own.

This lives beside ``agent_cli`` rather than inside it so ``cli`` can import the
one function without pulling in a Click command group.
"""

from pathlib import Path

from .agent_transport import client as daemon_client


def stop_agents_in_worktree(worktree_path: Path) -> list[str]:
    """Stop every daemon agent whose cwd is ``worktree_path``. One line each.

    Best-effort: a daemon that cannot be reached, or a stop it refuses, is
    reported and never raised. The close must not fail because the daemon is
    down, and the pid sweep that follows still tears the session down.
    """
    client = daemon_client()
    reply = client.request({"cmd": "list"})
    if reply.get("error"):
        return []
    wanted = str(worktree_path)
    messages = []
    for row in reply.get("agents", []):
        if row.get("cwd") != wanted:
            continue
        agent_id = row.get("id")
        stopped = client.request({"cmd": "stop", "id": agent_id})
        error = stopped.get("error")
        messages.append(f"agent {agent_id}: {error or 'stopped'}")
    return messages
