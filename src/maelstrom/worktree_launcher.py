"""Worktree launcher — open an editor or launch Claude in a worktree.

The placement/execution adapter for the worktree subsystem: it composes the
launch command and places it **inside cmux** — always. ``mael`` starts a Claude
session by driving the cmux socket; if the app is down it starts it, and if it
can't be reached it fails rather than running Claude locally. The only path that
runs Claude in the current terminal is the explicit ``--here`` choice, which
bypasses this module entirely (``task_cli._run_task`` calls ``exec_cmd``
directly). See memory ``project-launch-always-via-cmux-socket``.

Conceptually it belongs to the CLI/adapter layer of the three-layer split
documented in ``docs/dev/architecture-patterns.md`` (storage / model / CLI); it is
carved into its own file so the placement logic stays separate from the flat
``mael`` command handlers, not because it is a fourth architectural layer.

Commands are modelled as a closed ``ShellExpr`` algebra (see ``shell.py``); env
attaches per-``Command`` so it lands on the right pipe segment structurally, and
``shell.py`` owns both the executable argv (``to_argv``) and the human-readable
form (``describe``).

Import direction: this module imports ``run_cmd`` from the ``shell`` leaf and
``ensure_cmux_running`` from ``cmux.client``; ``worktree`` must never import this
module (nothing in it calls the launcher).
"""

import os
import subprocess
from enum import StrEnum
from pathlib import Path

import click

from .agent_model import build_start_payload
from .agent_transport import client as daemon_client
from .cmux import mael_layout
from .cmux.client import current_client, ensure_cmux_running
from .cmux.model import TerminalTab
from .config import load_config_or_default
from .harness_model import (
    HARNESS_CLAUDE,
    HARNESS_CODEX,
    HARNESS_OPENCODE,
    TRANSPORT_CLI,
    TRANSPORT_DAEMON,
    resolve_model_reference,
)
from .shell import (
    Command,
    Pipeline,
    ShellExpr,
    command_substitution,
    describe,
    run_cmd,
)


class AddContext(StrEnum):
    """The surface from which ``mael add`` was invoked."""

    REGULAR = "regular"
    CMUX = "cmux"
    DAEMON = "daemon"


def detect_add_context() -> AddContext:
    """Detect the ``add`` invocation surface.

    A driven agent takes precedence because its children inherit cmux variables.
    """
    if os.environ.get("MAEL_HARNESS_TYPE") == TRANSPORT_DAEMON:
        return AddContext.DAEMON
    return AddContext.CMUX if current_client() else AddContext.REGULAR


def start_install_async(worktree_path: Path) -> bool:
    """Start the configured installer without waiting for it."""
    install_cmd = load_config_or_default(worktree_path).install_cmd
    if not install_cmd:
        return False
    subprocess.Popen(
        ["sh", "-c", install_cmd],
        cwd=worktree_path,
        start_new_session=True,
    )
    return True


def has_install_command(worktree_path: Path) -> bool:
    """Whether this worktree configures an install command."""
    return bool(load_config_or_default(worktree_path).install_cmd)


async def start_agent_in_worktree(
    worktree_path: Path,
    *,
    permission_mode: str | None = None,
    env: dict[str, str] | None = None,
    session_id: str | None = None,
    resume: bool = False,
    model: str | None = None,
    prompt: str = "",
) -> str | None:
    """Start a driven agent and return its id without placing a cmux client."""
    ref = resolve_model_reference(model, permission_mode or "normal")
    if ref.harness != HARNESS_CLAUDE:
        raise ValueError(
            f"The {ref.harness} daemon is not available; use --cli with a {ref.harness}:* model."
        )
    agent_env = dict(env or {})
    if session_id:
        agent_env["MAEL_TASK_SESSION_ID"] = session_id
    payload = build_start_payload(
        worktree_path,
        permission_mode=permission_mode,
        env=agent_env,
        session_id=session_id,
        resume=resume,
        model=ref.alias,
        prompt=prompt,
    )
    reply = await daemon_client().request(payload)
    error = reply.get("error")
    agent_id = reply.get("id")
    if error or not agent_id:
        click.echo(
            f"Could not start the agent: {error or 'the daemon sent no agent id'}",
            err=True,
        )
        return None
    return str(agent_id)


async def launch_add_in_worktree(
    worktree_path: Path,
    project: str,
    worktree: str,
    *,
    context: AddContext,
    harness: str,
    no_agent: bool = False,
) -> bool:
    """Select the agent or shell surface for a prepared ``mael add`` worktree."""
    install_cmd = load_config_or_default(worktree_path).install_cmd or None
    if context == AddContext.CMUX:
        if no_agent:
            return mael_layout.ensure_worktree_shell_workspace(
                project, worktree, str(worktree_path), install_cmd=install_cmd
            )
        if harness == TRANSPORT_DAEMON:
            if not mael_layout.ensure_worktree_install_shell(
                project, worktree, str(worktree_path), install_cmd=install_cmd
            ):
                return False
            agent_id = await start_agent_in_worktree(worktree_path)
            if not agent_id:
                return False
            return mael_layout.add_worktree_agent(
                project,
                worktree,
                TerminalTab(
                    "Claude",
                    cwd=str(worktree_path),
                    command=describe(Command(["mael", "agent", "attach", agent_id])),
                ),
            )
        else:
            command = Command(build_harness_command())
        return mael_layout.ensure_worktree_workspace(
            project,
            worktree,
            str(worktree_path),
            command=describe(command),
            install_cmd=install_cmd,
        )

    if context == AddContext.DAEMON:
        if no_agent:
            return True
        if harness == TRANSPORT_CLI:
            if not ensure_cmux_running():
                return True
            return await launch_add_in_worktree(
                worktree_path,
                project,
                worktree,
                context=AddContext.CMUX,
                harness=harness,
            )
        agent_id = await start_agent_in_worktree(worktree_path)
        if agent_id:
            click.echo(f"Agent started: {agent_id}")
            click.echo(f"Attach with: mael agent attach {agent_id}")
        return agent_id is not None

    if no_agent:
        result = subprocess.run([os.environ.get("SHELL", "/bin/sh")], cwd=worktree_path)
        return result.returncode == 0
    if harness == TRANSPORT_DAEMON:
        agent_id = await start_agent_in_worktree(worktree_path)
        if agent_id:
            click.echo(f"Agent started: {agent_id}")
            click.echo(f"Attach with: mael agent attach {agent_id}")
        return agent_id is not None
    result = subprocess.run(build_harness_command(), cwd=worktree_path)
    return result.returncode == 0


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
    permission_mode: str | None = None,
    session_id: str | None = None,
    *,
    resume: bool = False,
    model: str | None = None,
) -> list[str]:
    """The trailing ``claude [...]`` argv shared by every placement (no env, no cwd).

    The initial prompt is no longer an argv argument — it is piped into ``claude``
    on stdin via :func:`build_task_launch_line`. When ``session_id`` is given it
    becomes ``--session-id``, pinning the task to a deterministic Claude session.
    ``--session-id`` sets the id the session *starts* with; it does not hold it
    for the life of the process, because a ``/clear`` starts a new conversation
    with a new id. The session reports that live id as ``CLAUDE_CODE_SESSION_ID``.

    ``model`` becomes ``--model`` — a free-form passthrough (an alias like ``opus``
    or a full id); falsy means "omit the flag", so the session inherits the user's
    Claude Code default. ``claude`` itself rejects an unknown value. A task launch
    never passes falsy: :func:`~maelstrom.task_launch.plan_launch` resolves an
    unset model to ``DEFAULT_MODEL`` first. Only a free agent omits the flag.

    ``--session-id`` *creates* a session and fails if one with that id already
    exists on disk. So when the task's session has run before (its transcript
    persists), pass ``resume=True``: the argv becomes ``claude --resume <id>``,
    which reattaches the existing conversation instead of trying to recreate it.
    The follow-up prompt still pipes in on stdin exactly as for a fresh launch.
    """
    argv = ["claude"]
    if permission_mode:
        argv += ["--permission-mode", permission_mode]
    if model:
        argv += ["--model", model]
    if session_id:
        argv += ["--resume", session_id] if resume else ["--session-id", session_id]
    return argv


def build_harness_command(
    permission_mode: str | None = None,
    session_id: str | None = None,
    *,
    resume: bool = False,
    model: str | None = None,
    harness: str | None = None,
) -> list[str]:
    """Build the direct CLI command selected by ``model``.

    ``harness`` remains an optional compatibility selector for callers that
    deliberately run another CLI. A model from the other prefix then supplies
    no model argument, so that CLI uses its configured default.
    """
    ref = resolve_model_reference(model, permission_mode or "normal")
    selected = harness or ref.harness
    if selected not in (HARNESS_CLAUDE, HARNESS_CODEX, HARNESS_OPENCODE):
        raise ValueError(f"Unknown CLI harness: {selected!r}")
    selected_ref = resolve_model_reference(
        f"{selected}:opus", permission_mode or "normal"
    )
    argv = [selected, *selected_ref.mode_args]
    if selected == ref.harness:
        argv += ref.cli_args
    if selected == HARNESS_CLAUDE and session_id:
        argv += ["--resume", session_id] if resume else ["--session-id", session_id]
    return argv


def build_task_launch_line(
    project: str,
    task_id: str,
    permission_mode: str | None = None,
    env: dict[str, str] | None = None,
    session_id: str | None = None,
    *,
    resume: bool = False,
    model: str | None = None,
    harness: str | None = None,
) -> ShellExpr:
    """Build a task prompt command for the CLI selected by its model."""
    command_env = dict(env or {})
    if session_id:
        command_env["MAEL_TASK_SESSION_ID"] = session_id
    prompt_argv = ["mael", "task", "prompt", task_id, "--project", project]
    harness_argv = build_harness_command(
        permission_mode, session_id, resume=resume, model=model, harness=harness
    )
    selected = (
        harness or resolve_model_reference(model, permission_mode or "normal").harness
    )
    if selected == HARNESS_CLAUDE:
        return Pipeline([Command(prompt_argv), Command(harness_argv, env=command_env)])
    prompt = command_substitution(prompt_argv)
    harness_argv.append(prompt)
    return Command(harness_argv, env=command_env)


def open_cmux_workspace(
    project: str | None,
    worktree: str | None,
    worktree_path: Path,
    command: ShellExpr,
) -> bool:
    """Open a cmux workspace that runs ``command`` in pane 0."""
    if not (project and worktree):
        return False
    install_cmd = load_config_or_default(worktree_path).install_cmd
    return mael_layout.ensure_worktree_workspace(
        project,
        worktree,
        str(worktree_path),
        command=describe(command),
        install_cmd=install_cmd or None,
    )


def open_claude_workspace(
    project: str | None,
    worktree: str | None,
    worktree_path: Path,
    command: ShellExpr,
) -> bool:
    """Compatibility name for :func:`open_cmux_workspace`."""
    return open_cmux_workspace(project, worktree, worktree_path, command)


async def launch_agent_in_worktree(
    worktree_path: Path,
    project: str | None,
    worktree: str | None,
    permission_mode: str | None = None,
    env: dict[str, str] | None = None,
    session_id: str | None = None,
    *,
    resume: bool = False,
    model: str | None = None,
    prompt: str = "",
) -> bool:
    """Start a daemon-driven agent, then place a pane that attaches to it.

    The daemon mints the agent id and the pane needs it, so this is two steps,
    not one: a ``start`` over the daemon socket, then the existing cmux
    placement with ``mael agent attach <id>`` as its command. Step 2 is still a
    pure :class:`ShellExpr`, so the build/execute split this module describes
    holds.

    ``session_id`` also rides in the agent's env as ``MAEL_TASK_SESSION_ID``,
    exactly as :func:`build_task_launch_line` does for the pipeline.

    cmux is started between the two steps, not before them: a start that fails
    must not leave the user with a cmux app they did not have running.

    Returns False when the daemon cannot be reached or answers without an id,
    after printing the daemon's own reason. The caller's message names cmux, so
    without the reason here the user is sent to restart a cmux that is fine.
    False also comes back when cmux cannot be started, and nothing is placed
    either way — an empty pane would be worse. The agent survives that: it is
    running, and ``mael agent attach`` reaches it.
    """
    agent_id = await start_agent_in_worktree(
        worktree_path,
        permission_mode=permission_mode,
        env=env,
        session_id=session_id,
        resume=resume,
        model=model,
        prompt=prompt,
    )
    if not agent_id:
        return False
    if not ensure_cmux_running():
        return False
    return open_claude_workspace(
        project,
        worktree,
        worktree_path,
        Command(["mael", "agent", "attach", agent_id]),
    )


async def launch_claude_in_worktree(
    worktree_path: Path,
    project: str | None,
    worktree: str | None,
    task_id: str | None = None,
    permission_mode: str | None = None,
    env: dict[str, str] | None = None,
    session_id: str | None = None,
    *,
    resume: bool = False,
    model: str | None = None,
    prompt: str = "",
    harness: str = TRANSPORT_CLI,
) -> bool:
    """Launch Claude for a worktree inside cmux. True if placed, False otherwise.

    **cmux-or-fail**: start the cmux app if it is down, then place a workspace.
    There is no local-execvp fallback — running Claude in the current process is
    the exclusive job of the explicit ``--here`` path, which bypasses this wrapper
    and calls ``exec_cmd`` with ``cwd=None`` directly.
    Returns False when cmux can't be started or the placement itself fails; the
    caller decides what to do (roll a task back to TODO, or raise).

    ``harness`` picks the runner; see ``HARNESSES``. The default ``daemon``
    hands the whole launch to :func:`launch_agent_in_worktree`. ``prompt`` is
    the opening prompt, which only the daemon path takes.

    On the legacy paths, with ``task_id`` (and ``project``) set, the command is
    the ``mael task prompt <id> | claude`` pipeline; otherwise it's a plain
    ``claude`` that just opens the worktree. ``session_id`` pins the
    deterministic Claude session id on the task path; ``resume`` reattaches an
    already-started session (``--resume`` vs ``--session-id``). ``model`` pins
    the session's LLM (``claude --model``). Either way env rides inside the
    ``ShellExpr``.
    """
    if harness == TRANSPORT_DAEMON:
        # The agent start comes first. cmux is only needed for the pane, and
        # starting the app before a launch that then fails would leave the user
        # with a cmux they did not have running.
        return await launch_agent_in_worktree(
            worktree_path,
            project,
            worktree,
            permission_mode=permission_mode,
            env=env,
            session_id=session_id,
            resume=resume,
            model=model,
            prompt=prompt,
        )
    if not ensure_cmux_running():
        return False
    if task_id and project:
        command: ShellExpr = build_task_launch_line(
            project,
            task_id,
            permission_mode,
            env=env,
            session_id=session_id,
            resume=resume,
            model=model,
            harness=None,
        )
    else:
        command = Command(
            build_harness_command(
                permission_mode,
                session_id,
                resume=resume,
                model=model,
                harness=None,
            ),
            env=dict(env or {}),
        )
    return open_claude_workspace(project, worktree, worktree_path, command)
