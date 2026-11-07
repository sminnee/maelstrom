"""Worktree launcher — open an editor or launch an agent in a worktree.

The execution adapter for the worktree subsystem. It composes the
launch command and runs it in one of two places: a daemon-driven agent, whose
attach command it prints, or the current terminal.

Conceptually it belongs to the CLI/adapter layer of the three-layer split
documented in ``docs/dev/architecture-patterns.md`` (storage / model / CLI); it is
carved into its own file so the launch logic stays separate from the flat
``mael`` command handlers, not because it is a fourth architectural layer.

Commands are modelled as a closed ``ShellExpr`` algebra (see ``shell.py``); env
attaches per-``Command`` so it lands on the right pipe segment structurally, and
``shell.py`` owns both the executable argv (``to_argv``) and the human-readable
form (``describe``).

Import direction: this module imports ``run_cmd`` from the ``shell`` leaf;
``worktree`` must never import this module (nothing in it calls the launcher).
"""

import os
import subprocess
import sys
from pathlib import Path

import click

from mael_agent.agent_transport import client as daemon_client
from mael_agent.agent_wire import build_start_payload
from mael_agent.harness_model import (
    CODEX_CORE_PLUGIN_ARG,
    HARNESS_CLAUDE,
    HARNESS_CODEX,
    HARNESS_OPENCODE,
    TRANSPORT_DAEMON,
    is_driven_agent,
    resolve_model_reference,
)
from mael_common.shell import (
    Command,
    Pipeline,
    ShellExpr,
    command_substitution,
    run_cmd,
)
from mael_domain import install_status
from mael_domain.config import load_config_or_default
from mael_domain.shared_dir import agent_prompt_file, core_plugin_dir


def start_install_async(worktree_path: Path) -> Path | None:
    """Start the configured installer without waiting for it.

    Returns its log, or ``None`` without an ``install_cmd``. The installer writes
    its exit code to the install status, which ``mael env status`` shows.
    """
    install_cmd = load_config_or_default(worktree_path).install_cmd
    if not install_cmd:
        return None
    names = install_status.names_from_path(worktree_path)
    argv = install_status.install_argv(*names, install_cmd)
    # Until its shell writes its own pid, this process holds the install, so a
    # start right after this does not read the last install's result.
    install_status.mark_running(*names, os.getpid())
    log = install_status.install_log_path(*names)
    with log.open("wb") as out:
        subprocess.Popen(
            argv,
            cwd=worktree_path,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return log


def _announce_started(agent_id: str) -> None:
    click.echo(f"Agent started: {agent_id}")
    click.echo(f"Attach with: mael agent attach {agent_id}")


async def start_agent_in_worktree(
    worktree_path: Path,
    *,
    permission_mode: str | None = None,
    env: dict[str, str] | None = None,
    session_id: str | None = None,
    resume: bool = False,
    model: str | None = None,
    execute_model: str | None = None,
    prompt: str = "",
) -> str | None:
    """Start a driven agent and return its id."""
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
        # Raw, not resolved: the daemon resolves it when the plan is approved,
        # and nothing spends it until then.
        execute_model=execute_model,
        prompt=prompt,
        system_prompt_file=agent_prompt_file(),
        plugin_dir=core_plugin_dir(),
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


NO_TERMINAL_REFUSAL = "--cli needs a terminal, and this process has none. Use --daemon."


def _streams_are_terminals() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def has_terminal() -> bool:
    """Whether this process can run an interactive harness or shell.

    A driven agent has no terminal. Nor has an agent's tool call outside the
    daemon: its stdin and stdout are pipes, and an exec there hangs the call.
    """
    return not is_driven_agent() and _streams_are_terminals()


async def launch_add_in_worktree(
    worktree_path: Path,
    *,
    terminal: bool,
    harness: str,
    model: str | None = None,
    execute_model: str | None = None,
    no_agent: bool = False,
) -> bool:
    """Start the agent or shell for a prepared ``mael add`` worktree.

    With no ``terminal``, ``--no-agent`` stops after the worktree, and the
    caller refuses ``--cli`` before it gets here. Otherwise ``--no-agent`` runs
    a subshell and ``--cli`` runs the harness in this terminal.
    """
    if no_agent:
        if not terminal:
            return True
        result = subprocess.run([os.environ.get("SHELL", "/bin/sh")], cwd=worktree_path)
        return result.returncode == 0
    if harness == TRANSPORT_DAEMON:
        agent_id = await start_agent_in_worktree(
            worktree_path, model=model, execute_model=execute_model
        )
        if agent_id:
            _announce_started(agent_id)
        return agent_id is not None
    result = subprocess.run(
        build_harness_command(model=model, plugin_dir=core_plugin_dir()),
        cwd=worktree_path,
    )
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
    never passes falsy: :func:`~mael_domain.task_launch.plan_launch` resolves an
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
    plugin_dir: Path | None = None,
) -> list[str]:
    """Build the direct CLI command selected by ``model``.

    ``harness`` remains an optional compatibility selector for callers that
    deliberately run another CLI. A model from the other prefix then supplies
    no model argument, so that CLI uses its configured default.

    ``plugin_dir`` is the core plugin, which ``claude`` loads with
    ``--plugin-dir``. ``codex`` has no such flag, so it always enables its
    installed copy by name, as a daemon thread does.
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
    if selected == HARNESS_CLAUDE and plugin_dir:
        argv += ["--plugin-dir", str(plugin_dir)]
    if selected == HARNESS_CODEX:
        argv += ["-c", CODEX_CORE_PLUGIN_ARG]
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
    plugin_dir: Path | None = None,
) -> ShellExpr:
    """Build a task prompt command for the CLI selected by its model."""
    command_env = dict(env or {})
    if session_id:
        command_env["MAEL_TASK_SESSION_ID"] = session_id
    prompt_argv = ["mael", "task", "prompt", task_id, "--project", project]
    harness_argv = build_harness_command(
        permission_mode,
        session_id,
        resume=resume,
        model=model,
        harness=harness,
        plugin_dir=plugin_dir,
    )
    selected = (
        harness or resolve_model_reference(model, permission_mode or "normal").harness
    )
    if selected == HARNESS_CLAUDE:
        return Pipeline([Command(prompt_argv), Command(harness_argv, env=command_env)])
    prompt = command_substitution(prompt_argv)
    harness_argv.append(prompt)
    return Command(harness_argv, env=command_env)


async def launch_agent_in_worktree(
    worktree_path: Path,
    permission_mode: str | None = None,
    env: dict[str, str] | None = None,
    session_id: str | None = None,
    *,
    resume: bool = False,
    model: str | None = None,
    execute_model: str | None = None,
    prompt: str = "",
) -> bool:
    """Start a daemon-driven agent and print the command that attaches to it.

    ``session_id`` also rides in the agent's env as ``MAEL_TASK_SESSION_ID``,
    exactly as :func:`build_task_launch_line` does for the pipeline. The
    installer runs in the background once the agent has started.

    Returns False when the daemon cannot be reached or answers without an id,
    after printing the daemon's own reason.
    """
    agent_id = await start_agent_in_worktree(
        worktree_path,
        permission_mode=permission_mode,
        env=env,
        session_id=session_id,
        resume=resume,
        model=model,
        execute_model=execute_model,
        prompt=prompt,
    )
    if not agent_id:
        return False
    start_install_async(worktree_path)
    _announce_started(agent_id)
    return True
