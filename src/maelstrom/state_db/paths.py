"""Where the state database is kept.

Every path here hangs off the *notebook root* — see
:mod:`maelstrom.notebook_root`, which explains why the notebook is named
separately from the rest of ``~/.maelstrom``.

Its own module because it is small and heavily patched: a test that redirects
these paths patches them here, and a re-export elsewhere would let such a patch
bind an unused alias while the real directory was still read.
"""

from pathlib import Path

from ..notebook_root import notebook_root


def get_state_db_path() -> Path:
    """Where the state database is kept.

    Raises:
        NotebookRootUnset: If the environment names no notebook root.
    """
    return notebook_root() / "state.db"


def get_desk_json_path() -> Path:
    """Where a desk written before the state database is kept.

    Read by the desk ladder's import rung, and by nothing else. It stays here
    so one module knows every path the database machinery reaches.

    Raises:
        NotebookRootUnset: If the environment names no notebook root.
    """
    return notebook_root() / "desk.json"


def get_notebook_path() -> Path:
    """Where a task notebook written before the state database is kept.

    Read by the tasks ladder's import rung, and by nothing else. It resolves the
    same directory as :func:`maelstrom.task_store.tasks_root`, but through this
    module rather than through it: a rung that reached the store's own accessor
    would read the developer's real notebook in every test, because the suite
    isolates the notebook by pinning the root this module reads and nothing
    else. The two must therefore move together.

    Raises:
        NotebookRootUnset: If the environment names no notebook root.
    """
    return notebook_root() / "tasks"
