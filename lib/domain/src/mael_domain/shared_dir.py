"""Where maelstrom's ``shared/`` directory is, and the files in it a client names.

One of them is built: the investigation prompt joins two shared files into
``~/.maelstrom/agent-prompts/``, because the daemon takes one file.
"""

import os
import tempfile
from pathlib import Path

from mael_common.util import get_maelstrom_dir


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


def investigation_prompt_file() -> Path | None:
    """The file for an investigation agent: the markers, then the no-code rules.

    The daemon takes one prompt file, so the two shared files are joined into
    ``~/.maelstrom/agent-prompts/investigation.md``. The file is rewritten only
    when its content changes, so a resume names a stable path. The rewrite is
    atomic: a spawn that reads it mid-write would run without the rules.

    ``None`` when either shared file is gone. Unlike the markers, the rules are
    the whole point of an investigation, so the caller refuses the start.
    """
    markers = agent_prompt_file()
    if markers is None:
        return None
    rules = markers.parent / "investigation-prompt.md"
    if not rules.exists():
        return None
    content = markers.read_text().rstrip("\n") + "\n\n" + rules.read_text()
    target = get_maelstrom_dir() / "agent-prompts" / "investigation.md"
    if target.exists() and target.read_text() == content:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    os.replace(tmp, target)
    return target
