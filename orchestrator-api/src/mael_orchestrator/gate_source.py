"""The orchestrator's one door onto tangier's gate jobs.

See "Gate job" in CONTEXT.md, and "Keeping the world fresh" in
docs/dev/orchestrator-server.md for why the read is async.
"""

import json
import subprocess
from typing import Any

from mael_common.shell import run_cmd_async

#: Seconds ``tangier gate status`` may take. A poll must not hang on it.
TANGIER_STATUS_TIMEOUT = 10


class GateSourceError(RuntimeError):
    """tangier could not list the gate jobs."""


async def gate_jobs() -> list[dict[str, Any]]:
    """Every running, then every queued, gate job on the machine, as tangier prints it.

    tangier 0.3.0 prints one object that groups the jobs: ``running``,
    ``queued`` and ``recent``, beside a ``machine`` summary. Under
    ``--running`` the ``recent`` list is empty, so only the first two count.

    Raises:
        GateSourceError: If ``tangier`` is missing, times out, fails or prints
            something other than a gate status object, such as an older
            tangier's flat list. An empty answer would read as "no jobs".
    """
    try:
        result = await run_cmd_async(
            ["tangier", "gate", "status", "--all", "--running", "--json"],
            quiet=True,
            check=False,
            timeout=TANGIER_STATUS_TIMEOUT,
        )
    except FileNotFoundError as e:
        raise GateSourceError("tangier is not on PATH") from e
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GateSourceError(f"tangier gate status failed: {e}") from e
    if result.returncode != 0:
        output = (result.stderr or result.stdout).strip()
        raise GateSourceError(
            f"tangier gate status failed (exit {result.returncode}): {output}"
        )
    try:
        status = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise GateSourceError(f"tangier gate status printed not JSON: {e}") from e
    if not isinstance(status, dict):
        raise GateSourceError("tangier gate status printed no gate status object")
    running, queued = status.get("running"), status.get("queued")
    if not (
        isinstance(running, list)
        and isinstance(queued, list)
        and all(isinstance(job, dict) and "id" in job for job in running + queued)
    ):
        raise GateSourceError("tangier gate status printed no gate status object")
    return running + queued
