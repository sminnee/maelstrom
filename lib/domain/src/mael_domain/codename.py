"""The codename word list, and the draw of a free word from it.

A codename is the word an open task shows in the orchestrator UI. See
CONTEXT.md, "Codename", and ``docs/dev/tasks.md``, "Codenames".

Stdlib only, so the tasks ladder can import it too.
"""

import secrets
from collections.abc import Callable, Sequence
from importlib.resources import files

#: Every codename, in the order of ``codenames.txt``.
CODENAMES: tuple[str, ...] = tuple(
    line.strip()
    for line in files("mael_domain").joinpath("codenames.txt").read_text().splitlines()
    if line.strip()
)


def pick_codename(
    held: set[str],
    *,
    choose: Callable[[Sequence[str]], str] = secrets.choice,
) -> str | None:
    """A random word that is not in ``held``, or ``None`` when all are held.

    ``choose`` is the test seam.
    """
    free = [word for word in CODENAMES if word not in held]
    return choose(free) if free else None


class CodenameTaken(Exception):
    """A save gave a task a codename that another task holds."""
