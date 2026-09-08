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
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import click

from .agent_model import build_start_payload
from .agent_transport import client as daemon_client
from .cmux import mael_layout
from .cmux.client import ensure_cmux_running
from .config import load_config_or_default
from .shell import (
    Command,
    Pipeline,
    ShellExpr,
    command_substitution,
    describe,
    run_cmd,
)

HARNESS_DAEMON = "daemon"
HARNESS_CLAUDE = "claude"
HARNESS_CODEX = "codex"
HARNESS_OPENCODE = "opencode"


@dataclass(frozen=True)
class Harness:
    """One harness's command, task capabilities, and workspace placement."""

    name: str
    command: tuple[str, ...] | None
    prompt_delivery: Literal["stdin", "argument", "option"] | None
    default: bool = False
    shorthand: str | None = None
    prompt_option: str | None = None
    detects_environment: str | None = None
    supports_task_session: bool = False
    supports_permission_mode: bool = False
    supports_model: bool = False
    uses_daemon: bool = False
    open_cmux_workspace: bool = True


HARNESS_REGISTRY = (
    Harness(
        HARNESS_DAEMON,
        None,
        None,
        default=True,
        supports_task_session=True,
        supports_permission_mode=True,
        supports_model=True,
        uses_daemon=True,
        open_cmux_workspace=True,
    ),
    Harness(
        HARNESS_CLAUDE,
        ("claude",),
        "stdin",
        shorthand="--claude",
        supports_task_session=True,
        supports_permission_mode=True,
        supports_model=True,
        open_cmux_workspace=True,
    ),
    Harness(
        HARNESS_CODEX,
        ("codex",),
        "argument",
        shorthand="--codex",
        open_cmux_workspace=True,
    ),
    Harness(
        HARNESS_OPENCODE,
        ("opencode2",),
        "option",
        shorthand="--opencode",
        prompt_option="--prompt",
        detects_environment="OPENCODE_TERMINAL",
        open_cmux_workspace=True,
    ),
)
HARNESSES = tuple(spec.name for spec in HARNESS_REGISTRY)


def harness_spec(name: str) -> Harness:
    """Return the registered harness called ``name``."""
    for spec in HARNESS_REGISTRY:
        if spec.name == name:
            return spec
    raise ValueError(f"Unknown harness: {name!r}")


def default_harness() -> Harness:
    """Return the one harness the registry marks as its default."""
    return next(spec for spec in HARNESS_REGISTRY if spec.default)


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
    harness: str = HARNESS_CLAUDE,
) -> list[str]:
    """Build a registered direct harness's command line."""
    spec = harness_spec(harness)
    if spec.uses_daemon or spec.command is None:
        raise ValueError(f"Harness {harness!r} has no direct command")
    argv = list(spec.command)
    if spec.supports_permission_mode and permission_mode:
        argv += ["--permission-mode", permission_mode]
    if spec.supports_model and model:
        argv += ["--model", model]
    if spec.supports_task_session and session_id:
        argv += ["--resume", session_id] if resume else ["--session-id", session_id]
    return argv


def _detect_harness_from_env() -> str | None:
    """The harness that launched the current shell, from its environment.

    Only OpenCode is detected (``OPENCODE_TERMINAL=1``), so an OpenCode user's
    ``mael task run`` stays in OpenCode. ``CLAUDECODE=1`` is not a signal: every
    session mael launches sets it, so detecting it would send every nested
    ``mael open`` back to the pane runner.
    """
    for spec in HARNESS_REGISTRY:
        if spec.detects_environment and os.environ.get(spec.detects_environment):
            return spec.name
    return None


def resolve_harness(harness: str | None, shortcuts: tuple[str, ...] = ()) -> str:
    """Merge ``--harness <name>`` with registry-derived shorthand flags.

    ``--harness`` is ``None`` when the flag was not given. Precedence, strongest
    first: an explicit flag (``--harness`` or a shorthand), then
    the environment the command runs in (a ``mael task run`` typed inside an
    OpenCode session launches OpenCode; ``--harness daemon`` overrides), then the
    default. Two flags that name different harnesses are a user error.
    """
    selected = set(shortcuts)
    if len(selected) > 1:
        flags = sorted(
            harness_spec(name).shorthand or f"--harness {name}" for name in selected
        )
        raise ValueError(f"{flags[0]} conflicts with {flags[1]}")
    if selected:
        selected_name = selected.pop()
        flag = harness_spec(selected_name).shorthand or f"--harness {selected_name}"
        if harness is not None and harness != selected_name:
            raise ValueError(f"{flag} conflicts with --harness {harness}")
        return selected_name
    if harness is None:
        return _detect_harness_from_env() or default_harness().name
    harness_spec(harness)
    return harness


def build_task_launch_line(
    project: str,
    task_id: str,
    permission_mode: str | None = None,
    env: dict[str, str] | None = None,
    session_id: str | None = None,
    *,
    resume: bool = False,
    model: str | None = None,
    harness: str = HARNESS_CLAUDE,
) -> ShellExpr:
    """Build a task's prompt command for the selected direct harness."""
    spec = harness_spec(harness)
    if spec.uses_daemon:
        raise ValueError(f"Harness {harness!r} has no direct task command")
    command_env = dict(env or {})
    if session_id:
        command_env["MAEL_TASK_SESSION_ID"] = session_id
    prompt_argv = ["mael", "task", "prompt", task_id, "--project", project]
    harness_argv = build_harness_command(
        permission_mode, session_id, resume=resume, model=model, harness=harness
    )
    if spec.prompt_delivery == "stdin":
        return Pipeline([Command(prompt_argv), Command(harness_argv, env=command_env)])
    prompt = command_substitution(prompt_argv)
    if spec.prompt_delivery == "option":
        assert spec.prompt_option is not None
        harness_argv += [spec.prompt_option, prompt]
    else:
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


def _open_harness_workspace(
    spec: Harness,
    project: str | None,
    worktree: str | None,
    worktree_path: Path,
    command: ShellExpr,
) -> bool:
    """Place a harness instruction when its registry entry allows cmux."""
    if not spec.open_cmux_workspace:
        return False
    return open_claude_workspace(project, worktree, worktree_path, command)


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
    agent_env = dict(env or {})
    if session_id:
        agent_env["MAEL_TASK_SESSION_ID"] = session_id
    payload = build_start_payload(
        worktree_path,
        permission_mode=permission_mode,
        env=agent_env,
        session_id=session_id,
        resume=resume,
        model=model,
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
        return False
    if not harness_spec(HARNESS_DAEMON).open_cmux_workspace:
        return True
    if not ensure_cmux_running():
        return False
    return open_claude_workspace(
        project,
        worktree,
        worktree_path,
        Command(["mael", "agent", "attach", str(agent_id)]),
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
    harness: str = HARNESS_DAEMON,
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
    spec = harness_spec(harness)
    if spec.uses_daemon:
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
            harness=harness,
        )
    else:
        command = Command(
            build_harness_command(
                permission_mode,
                session_id,
                resume=resume,
                model=model,
                harness=harness,
            ),
            env=dict(env or {}),
        )
    return _open_harness_workspace(spec, project, worktree, worktree_path, command)
