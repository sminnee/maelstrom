"""The orchestrator's one door onto tangier's gate jobs.

See "Gate job" and "Gate watch" in CONTEXT.md, and "The gate watch" in
docs/dev/orchestrator-server.md.
"""

import asyncio
import json
from collections import deque
from collections.abc import AsyncGenerator
from typing import Any

from mael_common.shell import kill_process_group

#: The lines of tangier's stderr an error keeps.
STDERR_TAIL = 5
#: The longest line the watch reads. The first ``--all`` status lists every
#: recent job on the machine, which passes asyncio's 64 KiB default.
LINE_LIMIT = 64 * 1024 * 1024


class GateSourceError(RuntimeError):
    """tangier's gate watch could not start, or stopped."""


async def watch_gates() -> AsyncGenerator[dict[str, Any]]:
    """Each change to the machine's gate jobs, as tangier prints it.

    ``tangier gate status --watch --json --all`` (tangier 0.3.1) prints a
    ``status`` snapshot, then one ``job`` or ``gate`` object per change. The
    watch never ends by itself; a cancel kills tangier's process group.

    Raises:
        GateSourceError: If ``tangier`` is missing, exits, or prints a line
            that is not a JSON object. An older tangier exits at once, and
            its stderr is in the message.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "tangier",
            "gate",
            "status",
            "--watch",
            "--json",
            "--all",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            limit=LINE_LIMIT,
        )
    except FileNotFoundError as e:
        raise GateSourceError("tangier is not on PATH") from e
    except OSError as e:
        raise GateSourceError(f"tangier gate status --watch failed: {e}") from e
    assert proc.stdout is not None and proc.stderr is not None
    # Read all the time: a full stderr pipe would stop tangier, and the watch
    # with it, with no error.
    stderr: deque[str] = deque(maxlen=STDERR_TAIL)
    drain = asyncio.create_task(_drain(proc.stderr, stderr))
    try:
        while line := await proc.stdout.readline():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                event = None
            if not isinstance(event, dict):
                raise GateSourceError(
                    f"tangier gate status --watch printed not a JSON object: "
                    f"{line.decode(errors='replace').strip()[:200]}"
                )
            yield event
        code = await proc.wait()
        await drain
        tail = "\n".join(stderr)
        raise GateSourceError(
            f"tangier gate status --watch ended (exit {code}): {tail}"
        )
    finally:
        if proc.returncode is None:
            kill_process_group(proc)
            # Reaped here: a cancelled caller must not leave a zombie.
            await proc.wait()
        drain.cancel()


async def _drain(stream: asyncio.StreamReader, tail: deque[str]) -> None:
    while line := await stream.readline():
        if text := line.decode(errors="replace").strip():
            tail.append(text)
