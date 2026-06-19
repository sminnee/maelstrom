"""Leaf utilities shared across the maelstrom core.

Pure helpers with no Click dependency and no domain knowledge: a UTC timestamp
formatter and an atomic JSON writer. Both were previously duplicated across
``task.py``, ``session_cli.py``, and ``env.py``; this is their single home.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string.

    Byte-identical to the previous ``_now_iso`` helpers in ``task.py`` and
    ``session_cli.py``; keep the exact ``datetime.now(timezone.utc).isoformat()``
    body so serialized timestamps round-trip unchanged.
    """
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(
    path: Path, data: Any, *, indent: int = 2, sort_keys: bool = True
) -> None:
    """Write ``data`` to ``path`` as JSON atomically.

    Writes to a sibling ``.tmp`` file then ``os.replace``s it into place so a
    crash mid-write can never leave a truncated file. Parent directories are
    created as needed. The ``indent=2, sort_keys=True`` defaults match the
    previous ``session_cli._atomic_write_json`` so existing files round-trip
    identically.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=indent, sort_keys=sort_keys)
    os.replace(tmp, path)
