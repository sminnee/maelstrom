"""Where maelstrom's ``shared/`` directory is, and the files in it a client names."""

from pathlib import Path


def get_shared_dir() -> Path:
    """Get path to maelstrom's shared/ directory.

    The published ``mael`` wheel carries it inside this package, because no
    repository surrounds it. A checkout has it at the repository root.
    """
    here = Path(__file__).parent
    # lib/domain/src/mael_domain/ -> the repository root.
    for candidate in (here / "shared", here.parents[3] / "shared"):
        if candidate.exists():
            return candidate
    raise FileNotFoundError("Could not locate maelstrom shared directory")


def agent_prompt_file() -> Path | None:
    """The file teaching a driven agent the markers, or ``None`` if it is gone.

    Shipped beside the shared skills, as ``claude-header.md`` is. A client
    names it in the daemon's ``start`` and ``resume``, because the daemon knows
    no shared dir. An installed tree that has lost it still launches agents:
    the markers go untaught, which costs a note, where a hard failure would
    cost the whole session.
    """
    try:
        prompt = get_shared_dir() / "agent-prompt.md"
    except FileNotFoundError:
        return None
    return prompt if prompt.exists() else None
