"""This build's schema: every ladder, every table, and the open that runs them.

The only module that changes when a subsystem joins the state database. It
gains an import, a ``LADDERS`` entry and a ``TABLES`` entry; nothing below it
moves.
"""

from pathlib import Path

from .db import StateDb
from .migrations.desk import DESK
from .migrations.spine import SPINE
from .types import Rung, TableSpec

#: Every subsystem's ladder, by name. A subsystem's schema moves without
#: dragging the others.
LADDERS: dict[str, tuple[Rung, ...]] = {"desk": DESK}

#: Every table a subsystem declares. The spine's own tables are not here: they
#: are the machinery, not rows a caller writes.
TABLES: dict[str, TableSpec] = {"desk": TableSpec("desk")}


def open_state_db(path: Path | str | None = None) -> StateDb:
    """A :class:`~maelstrom.state_db.db.StateDb` carrying this build's ladders.

    What every caller wants. A :class:`~maelstrom.state_db.db.StateDb` built
    directly knows no schema, which is what keeps the engine below the ladders
    it runs.

    :data:`LADDERS` is read here in the body rather than as a default argument,
    so a test that patches the dict is honoured on the next call.
    """
    return StateDb(path, ladders=LADDERS, tables=TABLES, spine=SPINE)
