"""Worktree launcher — open an editor or launch Claude in a worktree.

The placement/execution adapter for the worktree subsystem: it composes the
launch command (a plain ``claude`` argv, or the ``mael task prompt <id> | claude``
pipeline for a task) with a placement strategy — a cmux workspace when available,
else a process-replacing ``run_cmd(..., replace_process=True)`` in the worktree.
Conceptually it belongs to the CLI/adapter layer of the three-layer split
documented in ``docs/dev/architecture-patterns.md`` (storage / model / CLI); it is
carved into its own file so the placement logic stays separate from the flat
``mael`` command handlers, not because it is a fourth architectural layer.

Commands are modelled as a closed ``ShellExpr`` algebra (see ``shell.py``); env
attaches per-``Command`` so it lands on the right pipe segment structurally, and
``shell.py`` owns both the executable argv (``to_argv``) and the human-readable
form (``describe``).

Import direction: this module imports ``run_cmd`` from the ``shell`` leaf and
``run_install_cmd`` from ``worktree``; ``worktree`` must never import this module
(nothing in it calls the launcher).
"""

import subprocess
from pathlib import Path

from .config import load_config_or_default
from .cmux import mael_layout
from .shell import Command, Pipeline, ShellExpr, describe, run_cmd
from .worktree import run_install_cmd


def open_worktree(worktree_path: Path, command: str) -> None:
    """Open a worktree using the configured command.

    Args:
        worktree_path: Path to the worktree directory.
        command: Command to run (e.g., "code", "cursor").

    Raises:
        RuntimeError: If the command fails to execute.
    """
    try:
        run_cmd([command, str(worktree_path)])
    except FileNotFoundError:
        raise RuntimeError(f"Command not found: {command}")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to open worktree: {e}")


def build_claude_command(
    permission_mode: str | None = None, session_id: str | None = None
) -> list[str]:
    """The trailing ``claude [...]`` argv shared by every placement (no env, no cwd).

    The initial prompt is no longer an argv argument — it is piped into ``claude``
    on stdin via :func:`build_task_launch_line`. When ``session_id`` is given it
    becomes ``--session-id``, pinning the task to a deterministic Claude session
    (Claude then re-exports it as ``CLAUDE_SESSION_ID``).
    """
    argv = ["claude"]
    if permission_mode:
        argv += ["--permission-mode", permission_mode]
    if session_id:
        argv += ["--session-id", session_id]
    return argv


def build_task_launch_line(
    project: str,
    task_id: str,
    permission_mode: str | None = None,
    env: dict[str, str] | None = None,
    session_id: str | None = None,
) -> ShellExpr:
    """The pipeline that launches a task: ``mael task prompt ... | <env> claude ...``.

    The prompt is produced lazily by ``mael task prompt`` and piped into ``claude``
    on stdin, keeping the launch command line short. ``claude`` stays interactive
    because stdout remains a TTY (only stdin is piped).

    ``env`` vars attach to the ``claude`` :class:`Command` (the right of the pipe)
    so the interactive session inherits them. The structure makes the
    front-of-line scoping bug unrepresentable: env is a property of a single
    ``Command``, never of the whole ``Pipeline``. ``session_id`` pins the task's
    deterministic Claude session id (see :func:`build_claude_command`).
    """
    return Pipeline([
        Command(["mael", "task", "prompt", task_id, "--project", project]),
        Command(
            build_claude_command(permission_mode, session_id),
            env=dict(env or {}),
        ),
    ])


def open_claude_workspace(
    project: str | None,
    worktree: str | None,
    worktree_path: Path,
    command: ShellExpr,
) -> bool:
    """cmux placement: open a new workspace running the command. True if placed.

    Returns False (so the caller falls back to a process-replacing ``run_cmd``)
    when not in cmux or when project/worktree are missing — a workspace can't be
    named without them.

    A reused worktree with a live workspace gets a new Claude tab (carrying the
    same command line) rather than a duplicate workspace. Only the create path
    runs the install command, sent into the new workspace's shell pane so it
    runs visibly there; a reused workspace already installed.

    ``command`` is a :class:`ShellExpr`; cmux runs the workspace via a shell, so
    it receives the ``describe`` form, and a :class:`Command`'s env already rides
    on the correct pipe segment, so there is nothing to re-prefix here.
    """
    if not (project and worktree):
        return False

    install_cmd = load_config_or_default(worktree_path).install_cmd
    return mael_layout.ensure_worktree_workspace(
        project, worktree, str(worktree_path),
        command=describe(command),
        install_cmd=install_cmd or None,
    )


def launch_claude_in_worktree(
    worktree_path: Path,
    project: str | None,
    worktree: str | None,
    task_id: str | None = None,
    permission_mode: str | None = None,
    env: dict[str, str] | None = None,
    session_id: str | None = None,
) -> None:
    """Launch Claude for a worktree: new cmux workspace, else replace-in-place.

    The worktree-placement composition of the peers — the only thing the old
    ``start_claude_session`` actually provided. The ``--here`` path skips this
    wrapper and calls ``run_cmd(..., replace_process=True)`` with ``cwd=None``
    directly.

    With ``task_id`` (and ``project``) set, the command is the
    ``mael task prompt <id> | claude`` pipeline; otherwise it's a plain ``claude``
    that just opens the worktree. ``session_id`` pins the deterministic Claude
    session id on the task path. Either way env rides inside the ``ShellExpr``.
    """
    if task_id and project:
        command: ShellExpr = build_task_launch_line(
            project, task_id, permission_mode, env=env, session_id=session_id
        )
    else:
        command = Command(
            build_claude_command(permission_mode, session_id),
            env=dict(env or {}),
        )
    if not open_claude_workspace(project, worktree, worktree_path, command):
        # Non-cmux: no shell pane to run install in, so install blocking first.
        run_install_cmd(worktree_path)
        run_cmd(command, cwd=worktree_path, env=env, replace_process=True)
