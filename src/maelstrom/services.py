"""Command synthesis for structured ``services:`` definitions.

Pure model code (repo convention: ``docs/dev/architecture-patterns.md``). The
builders here turn a :class:`~maelstrom.config.ServiceDef` into the shell string
fed to the existing ``Popen(["sh", "-c", cmd])`` path — build-command is kept
separate from spawn (``[[feedback_separate_build_from_execute]]``).

The one I/O-touching function, :func:`discover_container_ip`, reaches its
``container inspect`` subprocess through an injectable ``runner`` (mirroring the
adapter pattern in ``branch_name.py``), so the model stays exercisable with a
fake runner and no container runtime.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass

from maelstrom.config import ServiceDef


@dataclass(frozen=True)
class Engine:
    """Per-engine binary + verb table keeping docker/apple-container DRY."""

    binary: str
    # Full argv (after the binary) that force-removes a container by name.
    rm_force: tuple[str, ...]
    # Extra flags appended to the ``run`` command (before the image).
    run_extra: tuple[str, ...]


ENGINES: dict[str, Engine] = {
    "docker": Engine(
        binary="docker",
        rm_force=("rm", "-f"),
        run_extra=("--stop-timeout", "2"),
    ),
    "apple-container": Engine(
        binary="container",
        rm_force=("delete", "--force"),
        run_extra=(),
    ),
}


def container_name(project: str, svc_name: str) -> str:
    """The container name for a service: ``<project>-<svcname>``."""
    return f"{project}-{svc_name}"


def volume_name(cname: str) -> str:
    """Derive a named-volume name from the container name."""
    return f"{cname}-data"


def build_command_service(svc: ServiceDef) -> str:
    """Build the shell string for a command (non-container) service.

    ``cd <dir> && <command>`` (dir optional). ``env:`` entries are *not* inlined
    here — they are merged into the spawn env dict by the caller.
    """
    assert svc.command is not None  # validated at parse time
    if svc.dir:
        return f"cd {svc.dir} && {svc.command}"
    return svc.command


def build_container_run(svc: ServiceDef, cname: str) -> str:
    """Synthesise the foreground container ``run`` boilerplate.

    ``cname`` is the container name (see :func:`container_name`); the caller
    passes it in so the naming rule lives in exactly one place. Produces
    ``<bin> rm-force <cname> ...; <bin> run --rm --name <cname> ...``: a
    best-effort force-remove of any stale container, then a **foreground**
    ``--rm`` run so the existing ``killpg`` stop path cleans up (matching today's
    foreground ``docker run --rm``). ``${VAR}`` references in ``publish`` / ``env``
    survive verbatim for the existing ``.env`` substitution to resolve.

    Raises:
        ValueError: If the service has no engine or an unknown one.
    """
    if svc.engine is None:
        raise ValueError(f"Service {svc.name!r}: not a container service")
    engine = ENGINES.get(svc.engine)
    if engine is None:
        raise ValueError(f"Service {svc.name!r}: unknown engine {svc.engine!r}")
    assert svc.image is not None  # validated at parse time

    bin_ = engine.binary

    rm = f"{bin_} {' '.join(engine.rm_force)} {cname} 2>/dev/null || true"

    run_parts = [bin_, "run", "--rm", "--name", cname]
    for mapping in svc.publish:
        run_parts += ["-p", mapping]
    if svc.volume:
        run_parts += ["-v", f"{volume_name(cname)}:{svc.volume}"]
    for key, value in svc.env.items():
        run_parts += ["-e", f"{key}={value}"]
    run_parts += list(engine.run_extra)
    run_parts.append(svc.image)
    run = " ".join(run_parts)

    return f"{rm}; {run}"


def discover_container_ip(
    name: str,
    runner: Callable[[list[str]], str],
    *,
    timeout: float = 10.0,
    interval: float = 0.3,
) -> str:
    """Poll ``container inspect <name>`` for the VM IP and return it (no CIDR).

    apple-container assigns each container a private ``192.168.64.x`` address that
    only appears once the VM has booted, so this polls ``inspect`` until the
    address is non-null or the deadline passes. The ``/24`` suffix is stripped.

    ``runner`` is invoked with the argv (e.g. ``["container", "inspect", name]``)
    and must return its stdout; tests inject a fake.

    Raises:
        TimeoutError: If the address stays null past ``timeout`` (→ start aborts,
            per the fail-loud decision — a missing host var means silent
            misconnection downstream).
    """
    deadline = time.monotonic() + timeout
    while True:
        ip = _parse_container_ip(runner(["container", "inspect", name]))
        if ip:
            return ip
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"container {name!r} did not report a network address within "
                f"{timeout:.0f}s"
            )
        time.sleep(interval)


def _parse_container_ip(inspect_stdout: str) -> str | None:
    """Extract ``data[0].networks[0].address`` (CIDR stripped) or None.

    Tolerant of an empty / not-yet-populated inspect payload: any missing key,
    empty list, or null address yields ``None`` so the caller keeps polling.
    """
    try:
        data = json.loads(inspect_stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None
    networks = data[0].get("networks")
    if not isinstance(networks, list) or not networks:
        return None
    if not isinstance(networks[0], dict):
        return None
    address = networks[0].get("address")
    if not address or not isinstance(address, str):
        return None
    return address.split("/", 1)[0]
