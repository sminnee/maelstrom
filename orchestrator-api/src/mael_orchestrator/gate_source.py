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
    """Every queued or running gate job on the machine, as tangier prints it.

    Raises:
        GateSourceError: If ``tangier`` is missing, times out, fails or prints
            something other than a JSON list of jobs. An empty answer would
            read as "no jobs".
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
        jobs = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise GateSourceError(f"tangier gate status printed not JSON: {e}") from e
    if not isinstance(jobs, list) or not all(
        isinstance(job, dict) and "id" in job for job in jobs
    ):
        raise GateSourceError("tangier gate status printed no JSON list of jobs")
    return jobs
