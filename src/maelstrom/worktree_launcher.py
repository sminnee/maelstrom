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
from pathlib import Path

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

# Harnesses mael can launch. ``daemon`` is the default: the agent daemon runs
# the ``claude`` child, and the cmux pane runs ``mael agent attach`` as a client
# of it, so the orchestrator UI sees the session. ``claude`` is the legacy pane
# runner — a bare ``claude`` in the pane, which nothing but the pane observes.
# ``opencode`` runs ``opencode2`` instead. OpenCode sessions cannot be pinned to
# a known session id (ids are server-assigned), so the session-id/resume
# machinery is claude-only.
HARNESS_DAEMON = "daemon"
HARNESS_CLAUDE = "claude"
HARNESS_OPENCODE = "opencode"

HARNESSES = (HARNESS_DAEMON, HARNESS_CLAUDE, HARNESS_OPENCODE)


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
    Claude Code default. ``claude`` itself rejects an unknown value.

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
    """The trailing harness argv — :func:`build_claude_command` dispatched by harness.

    ``claude`` (default) is byte-identical to the historic argv. ``opencode``
    is a bare ``opencode2``: v1 passes no permission, model or session flags —
    opencode has no equivalent of ``--session-id`` (ids are server-assigned),
    and permission modes were deliberately left unmapped.
    """
    if harness == HARNESS_OPENCODE:
        return ["opencode2"]
    if harness == HARNESS_CLAUDE:
        return build_claude_command(
            permission_mode, session_id, resume=resume, model=model
        )
    raise ValueError(f"Unknown harness: {harness!r}")


def _detect_harness_from_env() -> str | None:
    """The harness that launched the current shell, from its environment.

    Only OpenCode is detected (``OPENCODE_TERMINAL=1``), so an OpenCode user's
    ``mael task run`` stays in OpenCode. ``CLAUDECODE=1`` is not a signal: every
    session mael launches sets it, so detecting it would send every nested
    ``mael open`` back to the pane runner.
    """
    if os.environ.get("OPENCODE_TERMINAL"):
        return HARNESS_OPENCODE
    return None


def resolve_harness(harness: str | None, opencode: bool, claude: bool) -> str:
    """Merge ``--harness <name>`` with the ``--opencode`` / ``--claude`` shorthands.

    ``--harness`` is ``None`` when the flag was not given. Precedence, strongest
    first: an explicit flag (``--harness``, ``--opencode`` or ``--claude``), then
    the environment the command runs in (a ``mael task run`` typed inside an
    OpenCode session launches OpenCode; ``--harness daemon`` overrides), then the
    daemon. Two flags that name different harnesses are a user error, whether
    they are two shorthands (``--claude --opencode``) or a shorthand and a
    ``--harness`` value (``--harness claude --opencode``).
    """
    if opencode and claude:
        raise ValueError("--claude conflicts with --opencode")
    if opencode:
        if harness is not None and harness != HARNESS_OPENCODE:
            raise ValueError(f"--opencode conflicts with --harness {harness}")
        return HARNESS_OPENCODE
    if claude:
        if harness is not None and harness != HARNESS_CLAUDE:
            raise ValueError(f"--claude conflicts with --harness {harness}")
        return HARNESS_CLAUDE
    if harness is None:
        return _detect_harness_from_env() or HARNESS_DAEMON
    if harness not in HARNESSES:
        raise ValueError(f"Unknown harness: {harness!r}")
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
    """The pipeline that launches a task: ``mael task prompt ... | <env> claude ...``.

    The prompt is produced lazily by ``mael task prompt`` and piped into ``claude``
    on stdin, keeping the launch command line short. ``claude`` stays interactive
    because stdout remains a TTY (only stdin is piped).

    ``env`` vars attach to the ``claude`` :class:`Command` (the right of the pipe)
    so the interactive session inherits them. The structure makes the
    front-of-line scoping bug unrepresentable: env is a property of a single
    ``Command``, never of the whole ``Pipeline``. ``session_id`` pins the task's
    deterministic Claude session id (see :func:`build_claude_command`) and also
    rides as ``MAEL_TASK_SESSION_ID`` so the session-channel keys the
    ``~/.maelstrom`` registry on it, which is what ``reconcile`` and
    ``session list`` match on.

    The name pairs with ``MAEL_TASK_ID`` / ``MAEL_TASK_PARENT`` because that is
    what it is: a **task key**, not a reference to the conversation running now.
    The harness does export a live session id (``CLAUDE_CODE_SESSION_ID``), but a
    ``/clear`` moves it, so it cannot key a task. The derived id never moves.
    """
    claude_env = dict(env or {})
    if session_id:
        claude_env["MAEL_TASK_SESSION_ID"] = session_id
    prompt_argv = ["mael", "task", "prompt", task_id, "--project", project]
    if harness == HARNESS_OPENCODE:
        # No session id and no claude flags: opencode takes the prompt as a
        # ``--prompt`` argument, so the lazily-produced `mael task prompt`
        # output reaches it through a quoted command substitution instead of a
        # stdin pipe. Everything claude-only (permission mode, model, resume)
        # is deliberately dropped.
        return Command(
            [
                "opencode2",
                "--prompt",
                command_substitution(prompt_argv),
            ],
            env=claude_env,
        )
    return Pipeline(
        [
            Command(prompt_argv),
            Command(
                build_claude_command(
                    permission_mode, session_id, resume=resume, model=model
                ),
                env=claude_env,
            ),
        ]
    )


def open_claude_workspace(
    project: str | None,
    worktree: str | None,
    worktree_path: Path,
    command: ShellExpr,
) -> bool:
    """cmux placement: open a new workspace running the command. True if placed.

    Returns False when not in cmux or when project/worktree are missing — a
    workspace can't be named without them. The caller treats False as a placement
    failure (there is no local fallback).

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
        project,
        worktree,
        str(worktree_path),
        command=describe(command),
        install_cmd=install_cmd or None,
    )


def launch_agent_in_worktree(
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
    reply = daemon_client().request(payload)
    error = reply.get("error")
    agent_id = reply.get("id")
    if error or not agent_id:
        click.echo(
            f"Could not start the agent: {error or 'the daemon sent no agent id'}",
            err=True,
        )
        return False
    if not ensure_cmux_running():
        return False
    return open_claude_workspace(
        project,
        worktree,
        worktree_path,
        Command(["mael", "agent", "attach", str(agent_id)]),
    )


def launch_claude_in_worktree(
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
    if harness == HARNESS_DAEMON:
        # The agent start comes first. cmux is only needed for the pane, and
        # starting the app before a launch that then fails would leave the user
        # with a cmux they did not have running.
        return launch_agent_in_worktree(
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
    return open_claude_workspace(project, worktree, worktree_path, command)
