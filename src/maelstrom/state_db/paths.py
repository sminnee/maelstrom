"""Where the state database is kept.

Its own module because it is small and heavily patched: a test that redirects
``get_maelstrom_dir`` patches it here, and a re-export elsewhere would let such
a patch bind an unused alias while the real directory was still read.
"""

from pathlib import Path

from ..context import get_maelstrom_dir


def get_state_db_path() -> Path:
    """Where the state database is kept."""
    return get_maelstrom_dir() / "state.db"


def get_desk_json_path() -> Path:
    """Where a desk written before the state database is kept.

    Read by the desk ladder's import rung, and by nothing else. It stays here
    so one module knows every path the database machinery reaches.
    """
    return get_maelstrom_dir() / "desk.json"


def get_notebook_path() -> Path:
    """Where a task notebook written before the state database is kept.

    Read by the tasks ladder's import rung, and by nothing else. It resolves the
    same directory as :func:`maelstrom.task_store.tasks_root`, but through this
    module rather than through it: a rung that reached the store's own accessor
    would read the developer's real notebook in every test, because the suite
    isolates ``~/.maelstrom`` by patching *this* module's
    ``get_maelstrom_dir`` and nothing else.
    """
    return get_maelstrom_dir() / "tasks"
