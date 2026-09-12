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
