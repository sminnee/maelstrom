"""Name a comm with a **Comm codename**. See ``docs/specs/tasks/CONTEXT.md``, "Comm codename".

The **Task metadata generator** makes the codename. The model only proposes.
This module checks the reply against a strict format and against the codenames
in use, so a codename is unique whatever the model says.
"""

from __future__ import annotations

import logging
import re

from . import task_metadata_generator
from .task_metadata_generator import SlugGenerator, TaskMetadataGenerator

log = logging.getLogger(__name__)

#: One word, or two, each in Title Case.
CODENAME_RE = re.compile(r"^[A-Z][a-z]+( [A-Z][a-z]+)?$")

_SYSTEM_PROMPT = (
    "You give projects codenames. Your only job is to emit one codename on "
    "one line. Do not explain, do not ask questions, do not run tools."
)


def _build_prompt(title: str, content: str, taken: set[str]) -> str:
    snippet = content.strip()[:800]
    body = f"Title: {title}"
    if snippet:
        body += f"\n\nDetails:\n{snippet}"
    used = ", ".join(sorted(taken)) or "none"
    return (
        "Give this piece of work a codename. Reply with EXACTLY ONE LINE: one "
        "evocative English word, or two at most, letters only. It does not "
        "need to use the words of the title.\n"
        "Example: Lantern\n"
        f"Do not use a codename in this list: {used}\n\n"
        f"{body}"
    )


def normalise(raw: str) -> str:
    """``raw`` in Title Case with single spaces, or ``""`` when it is not a codename."""
    words = raw.strip().strip(".\"'`").split()
    name = " ".join(w.capitalize() for w in words)
    return name if CODENAME_RE.match(name) else ""


def is_taken(name: str, taken: set[str]) -> bool:
    """Whether ``name`` is in ``taken``, case ignored."""
    return name.lower() in {t.lower() for t in taken}


def generate_codename(
    title: str,
    content: str,
    taken: set[str],
    generator: TaskMetadataGenerator | None = None,
) -> str:
    """A codename for a comm that no comm in ``taken`` uses, or ``""``.

    Blocking: it calls the generator. ``generator`` defaults to
    ``default_generator()``. A :class:`SlugGenerator` returns ``""`` at once.
    """
    gen = generator or task_metadata_generator.default_generator()
    if isinstance(gen, SlugGenerator) or not title.strip():
        return ""
    prompt = _build_prompt(title, content, taken)
    for _ in range(2):
        try:
            raw = gen.complete(prompt, _SYSTEM_PROMPT)
        except Exception as e:
            log.warning("Comm codename call failed: %s", e)
            continue
        lines = raw.strip().splitlines()
        name = normalise(lines[0]) if lines else ""
        if name and not is_taken(name, taken):
            return name
    return ""
