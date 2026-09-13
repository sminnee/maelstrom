"""Where the state database is kept.

Its own module because it is small and heavily patched: the paths here call
:func:`maelstrom.context.get_state_root` and
:func:`maelstrom.context.get_maelstrom_dir` in a body, so a test that redirects
either reaches this module's binding. A re-export elsewhere would let such a
patch bind an unused alias while the real directory was still read.

Only the database follows the state root. ``desk.json`` and the notebook stay on
the shared root, which is what makes a fresh playpen start empty.
"""

from pathlib import Path

from ..context import get_maelstrom_dir, get_state_root


def get_state_db_path() -> Path:
    """Where the state database is kept.

    Under the state root, so ``uv run mael`` in a worktree reaches that
    worktree's playpen and the ``mael`` on the PATH reaches the real notebook.
    """
    return get_state_root() / "state.db"


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
