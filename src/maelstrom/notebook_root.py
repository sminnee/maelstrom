"""Where the task notebook is kept.

The notebook is ``state.db`` and the markdown task export beside it. Its root
comes from ``MAEL_NOTEBOOK_ROOT``, which each environment writes into its own
``.env``. There is no fallback: a command that finds no root has no notebook to
write to, rather than someone else's.

Its own module rather than :mod:`maelstrom.context`, because
:func:`~maelstrom.context.get_maelstrom_dir` must keep its unconditional
meaning for everything else under ``~/.maelstrom`` — ports, logs, ``envs/``,
``config.yaml`` and ``daemons/``. Refusing there would break ``mael env start``
in every worktree, which is far wider than the hazard.

The hazard is narrow and real. ``mael`` runs ``_main``'s code and ``uv run
mael`` runs the current worktree's, but both wrote to the one notebook, so a
command run to *test* a feature *performed* it: a planning agent testing ``task
promote`` planned a real task. :mod:`maelstrom.agent_transport` met the same
problem for the daemon and answered it the same way.
"""

import os
from pathlib import Path

#: Names the notebook root. The one variable that moves every notebook path at
#: once.
NOTEBOOK_ROOT_ENV = "MAEL_NOTEBOOK_ROOT"

#: What to tell someone whose environment names no root. Names the repair,
#: because on a machine whose `.env` predates this variable the message is the
#: only thing the user gets.
NOTEBOOK_ROOT_UNSET_MESSAGE = (
    f"{NOTEBOOK_ROOT_ENV} is not set, so there is no notebook to read or "
    f"write. Add {NOTEBOOK_ROOT_ENV}=~/.maelstrom/notebooks/${{WORKTREE}} to "
    "the project root's `.env` and run `mael env reset`, or set it for one "
    f"command: {NOTEBOOK_ROOT_ENV}=~/.maelstrom mael …"
)


class NotebookRootUnset(Exception):
    """Raised when :data:`NOTEBOOK_ROOT_ENV` names no notebook root.

    The one failure that has no safe default. A guessed root reaches the real
    notebook, which is how a worktree's test code came to plan a real task.
    """

    def __init__(self) -> None:
        super().__init__(NOTEBOOK_ROOT_UNSET_MESSAGE)


def notebook_root() -> Path:
    """The notebook root named by :data:`NOTEBOOK_ROOT_ENV`.

    ``~`` is expanded: the value is written by hand into ``.env``, so a tilde
    reaches here, and an unexpanded one makes a directory named ``~`` wherever
    the process happens to be running.

    Raises:
        NotebookRootUnset: If the variable is absent or empty.
    """
    override = os.environ.get(NOTEBOOK_ROOT_ENV)
    if not override:
        raise NotebookRootUnset()
    return Path(override).expanduser()
