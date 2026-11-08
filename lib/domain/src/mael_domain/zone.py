"""The **Schedule zone**: which time zone a template schedule is read in.

``schedule_timezone:`` in the config names it; :func:`zone_named` turns the name
into a zone, and :func:`host_zone` is the default. See CONTEXT.md, "Schedule zone".
"""

import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def host_zone(localtime: Path = Path("/etc/localtime")) -> ZoneInfo:
    """The host's own zone, DST-aware, or UTC when it cannot be found.

    Tries the ``TZ`` env var, then the key in the ``localtime`` symlink target
    (``.../zoneinfo/<key>`` on Linux and macOS), then the ``localtime`` file.
    ``datetime.now().astimezone()`` gives a fixed offset, which is wrong across
    DST, so it is not used.
    """
    name = os.environ.get("TZ", "").lstrip(":")
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    try:
        target = str(localtime.resolve())
    except OSError:
        target = ""
    _, sep, key = target.partition("zoneinfo/")
    if sep and key:
        try:
            return ZoneInfo(key)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    try:
        with localtime.open("rb") as f:
            return ZoneInfo.from_file(f, key="localtime")
    except (OSError, ValueError):
        return ZoneInfo("UTC")


def zone_named(name: str | None) -> ZoneInfo:
    """The zone ``name`` names, or :func:`host_zone` when it is empty.

    Raises ``ValueError`` on an unknown name.
    """
    if not name:
        return host_zone()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown zone {name}") from exc
