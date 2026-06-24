"""Worktree launcher — open an editor or launch Claude in a worktree.

The placement/execution adapter for the worktree subsystem: it composes the
launch command (a plain ``claude`` argv, or the ``mael task prompt <id> | claude``
pipeline for a task) with a placement strategy — a cmux workspace when available,
else an ``execvp`` in the worktree. Conceptually it belongs to the CLI/adapter
layer of the three-layer split documented in ``docs/dev/architecture-patterns.md``
(storage / model / CLI); it is carved into its own file so the placement logic
stays separate from the flat ``mael`` command handlers, not because it is a fourth
architectural layer.

Import direction: this module imports ``run_install_cmd`` from ``worktree``;
``worktree`` must never import this module (nothing in it calls the launcher).
"""

import os
import shlex
import subprocess
from pathlib import Path
from typing import NoReturn

from .config import load_config_or_default
from .cmux import mael_layout
from .worktree_model import claude_shell_line, env_prefixed
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
        subprocess.run([command, str(worktree_path)], check=True)
    except FileNotFoundError:
        raise RuntimeError(f"Command not found: {command}")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to open worktree: {e}")


def build_claude_command(permission_mode: str | None = None) -> list[str]:
    """The trailing ``claude [...]`` argv shared by every placement (no env, no cwd).

    The initial prompt is no longer an argv argument — it is piped into ``claude``
    on stdin via :func:`build_task_launch_line`.
    """
    argv = ["claude"]
    if permission_mode:
        argv += ["--permission-mode", permission_mode]
    return argv


def pipe_cmd(*segments: str) -> str:
    """Join already-shell-safe command strings into a pipeline."""
    return " | ".join(segments)


def build_task_launch_line(
    project: str,
    task_id: str,
    permission_mode: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """The shell pipeline that launches a task: ``mael task prompt ... | <env> claude ...``.

    The prompt is produced lazily by ``mael task prompt`` and piped into ``claude``
    on stdin, keeping the launch command line short. ``claude`` stays interactive
    because stdout remains a TTY (only stdin is piped).

    ``env`` vars are prefixed onto the ``claude`` segment (the right of the pipe) so
    the interactive session inherits them — a front-of-line prefix would only reach
    ``mael task prompt`` (POSIX scopes a ``KEY=val`` prefix to the first command of a
    pipeline).
    """
    task_cmd = shlex.join(["mael", "task", "prompt", task_id, "--project", project])
    claude = env_prefixed(shlex.join(build_claude_command(permission_mode)), env)
    return pipe_cmd(task_cmd, claude)


def exec_claude(
    command: list[str] | str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> NoReturn:
    """Replace this process with the launch command. chdir to ``cwd`` first if given.

    ``command`` is either a ``claude`` argv list (plain worktree open) or a shell
    pipeline string (a task launch). A pipeline is run via ``sh -c "exec ..."`` so
    the process is replaced while stdin/stdout/stderr are inherited — stdout stays
    a TTY, so ``claude`` runs interactively.

    ``cwd=None`` means "right here" (the ``--here`` path); a worktree path means
    the old execvp-fallback behaviour.
    """
    if cwd is not None:
        os.chdir(cwd)
    if env:
        os.environ.update(env)
    if isinstance(command, str):
        os.execvp("sh", ["sh", "-c", f"exec {command}"])
    else:
        os.execvp("claude", command)


def open_claude_workspace(
    project: str | None,
    worktree: str | None,
    worktree_path: Path,
    command: list[str] | str,
    env: dict[str, str] | None = None,
) -> bool:
    """cmux placement: open a new workspace running the command. True if placed.

    Returns False (so the caller falls back to ``exec_claude``) when not in
    cmux or when project/worktree are missing — a workspace can't be named
    without them.

    A reused worktree with a live workspace gets a new Claude tab (carrying the
    same command line) rather than a duplicate workspace. Only the create path
    runs the install command, sent into the new workspace's shell pane so it
    runs visibly there; a reused workspace already installed.
    """
    if not (project and worktree):
        return False

    # Idempotent: creates the 3-pane workspace if absent, else adds a Claude tab
    # to a live one. Returns False outside cmux. The install command runs in the
    # shell pane (a reused workspace already installed, but the create path needs
    # it). Returns False so the caller falls back to ``exec_claude``.
    # A pipeline string already carries the env prefix on its ``claude`` segment
    # (the right of the pipe, where the interactive session needs it); a plain
    # argv list is shell-quoted with a front prefix, which is correct because
    # there is no pipe to scope it away.
    shell_line = (
        command
        if isinstance(command, str)
        else claude_shell_line(command, env)
    )
    install_cmd = load_config_or_default(worktree_path).install_cmd
    return mael_layout.ensure_worktree_workspace(
        project, worktree, str(worktree_path),
        command=shell_line,
        install_cmd=install_cmd or None,
    )


def launch_claude_in_worktree(
    worktree_path: Path,
    project: str | None,
    worktree: str | None,
    task_id: str | None = None,
    permission_mode: str | None = None,
    env: dict[str, str] | None = None,
) -> None:
    """Launch Claude for a worktree: new cmux workspace, else execvp in it.

    The worktree-placement composition of the peers — the only thing the old
    ``start_claude_session`` actually provided. The ``--here`` path skips this
    wrapper and calls :func:`exec_claude` with ``cwd=None`` directly.

    With ``task_id`` (and ``project``) set, the command is the
    ``mael task prompt <id> | claude`` pipeline; otherwise it's a plain ``claude``
    that just opens the worktree.
    """
    if task_id and project:
        command: list[str] | str = build_task_launch_line(
            project, task_id, permission_mode, env=env
        )
    else:
        command = build_claude_command(permission_mode)
    if not open_claude_workspace(project, worktree, worktree_path, command, env):
        # Non-cmux: no shell pane to run install in, so install blocking first.
        run_install_cmd(worktree_path)
        exec_claude(command, cwd=worktree_path, env=env)
