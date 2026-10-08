"""Where the state database is kept.

Every path here hangs off the *notebook root* — see
:mod:`mael_domain.notebook_root`, which explains why the notebook is named
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


def desk_json_path(root: Path) -> Path:
    """Where a desk written before the state database is kept, under ``root``.

    Read by the desk ladder's import rung, and by nothing else. It stays here
    so one module knows every path the database machinery reaches.
    """
    return root / "desk.json"


def notebook_path(root: Path) -> Path:
    """Where a markdown task notebook is kept, under ``root``."""
    return root / "tasks"


def get_notebook_path() -> Path:
    """The markdown task notebook under the environment's notebook root.

    It resolves the same directory as :func:`mael_domain.task_store.tasks_root`,
    so the two must move together.

    Raises:
        NotebookRootUnset: If the environment names no notebook root.
    """
    return notebook_path(notebook_root())
