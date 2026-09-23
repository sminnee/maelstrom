"""Closed command algebra: a small, auditable shell-expression grammar.

A ``ShellExpr`` is a closed union; the supported shell-feature set is deliberately
small and fixed — adding a node means editing this union AND the two matches below,
so the surface stays reviewable. ``list[str]`` (a bare argv) is a first-class member.

The public entry points, all owned here so callers stay shell-agnostic:

- ``describe`` — the human-readable shell string, for echo lines and test
  assertions. Display only; nothing executes it.
- ``to_argv`` — the argv that actually executes the expression. A bare argv runs
  directly (no shell, so no quoting round-trip and no injection surface); a
  ``Command``/``Pipeline`` uses shell syntax (pipes, ``KEY=val`` prefixes) so it
  goes through ``sh -c``. Whether a shell is needed is a property of the *node*,
  decided once here — not a flag the caller toggles.
- ``run_cmd`` — fork-and-wait, returning a ``CompletedProcess``.
- ``exec_cmd`` — exec-replace, never returning. Split from ``run_cmd`` because an
  exec has no result to check, no output to capture, and no wait to bound, so
  those options would be dead weight on one of the two paths.
- ``run_cmd_async`` — ``run_cmd``'s contract on an event loop, for a caller
  that holds one for many jobs: same arguments, same ``CompletedProcess``,
  same raise on a non-zero exit. A blocking caller converts by adding
  ``await``. It is the only async runner, so no call site has to work out
  which of two near-identical names it is looking at.

``run_cmd``, ``exec_cmd`` and ``run_cmd_async`` together are the execution
chokepoint: every command in the codebase routes through one of them, so they
are the seam to mock / log / intercept. Each writes the command it runs to this
module's logger, so one handler sees every command whatever the caller.

These functions own the accidental complexity of running a command — quoting,
whether a shell is needed, process groups, decoding, the deadline. A caller
states what to run and reads the result.

Quoting/escaping happens in exactly one place: ``_shell_string``. This module is
a leaf — it imports only stdlib, so anything may depend on it without cycles.
"""

import asyncio
import logging
import os
import shlex
import shutil
import signal
import subprocess
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, NoReturn, assert_never


@dataclass(frozen=True)
class Command:
    """A single argv with optional per-command ``KEY=val`` env assignments.

    Env attaches HERE (per command), not at the pipeline, so it lands on the
    correct pipe segment — POSIX scopes a front-of-line ``KEY=val`` prefix to only
    the first stage of a pipeline. There is no way to express "env on the whole
    pipeline", so the just-fixed MAEL_TASK_ID bug is structurally unrepresentable.
    """

    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Pipeline:
    """``a | b | c``. Each stage is a ``ShellExpr`` (normally a ``Command``/argv)."""

    stages: list["ShellExpr"]


class RawShell(str):
    """A shell command as raw text, for the one case that is genuinely text.

    Every other member of :data:`ShellExpr` is structured, so ``to_argv`` can
    run a bare argv with no shell at all. A ``RawShell`` cannot be: it holds
    what a user typed after a ``!``, and pipes and redirection are what they
    typed it for. So it always goes through ``sh -c``, and whatever it contains
    is shell syntax rather than data.

    It is a ``str`` subclass and not a bare ``str`` on purpose. A caller must
    name it to get this behaviour, and ``grep -rn RawShell`` finds every place
    the codebase executes text it did not build. Prefer an argv or a
    :class:`Command` wherever the caller knows the parts.
    """


# Closed union. ``list[str]`` is a literal member (the zero-ceremony base case);
# ``RawShell`` is the deliberately-awkward member for user-typed text.
ShellExpr = list[str] | Command | Pipeline | RawShell


class Subst(str):
    """A single argv element that is a pre-rendered, quoted command substitution.

    ``str`` subclass so it can sit inside a plain ``argv: list[str]`` while
    rendering as ``"$(...)"`` instead of a literal word. Built by
    :func:`command_substitution`; never hand-constructed (the inner argv must
    be shlex-joined, which is that function's one job).
    """


def command_substitution(argv: list[str]) -> Subst:
    """Render ``argv`` as a quoted ``"$(...)"`` argv element.

    Double-quoted so a multi-word/multi-line result stays one shell word. The
    inner ``shlex.join`` output only ever contains single quotes, so it cannot
    break out of the surrounding double quotes.
    """
    return Subst(f'"$({shlex.join(argv)})"')


def _join_argv(argv: list[str]) -> str:
    """Join argv into shell words, leaving :class:`Subst` elements verbatim."""
    return " ".join(s if isinstance(s, Subst) else shlex.quote(s) for s in argv)


def _shell_string(expr: ShellExpr) -> str:
    """Render ``expr`` to a shell string. The ONLY place quoting/escaping happens."""
    match expr:
        case RawShell():  # already shell text — nothing to quote
            return str(expr)
        case list():  # bare argv — base case
            return _join_argv(expr)
        case Command(argv, env):
            prefix = "".join(f"{k}={shlex.quote(v)} " for k, v in env.items())
            return f"{prefix}{_join_argv(argv)}"
        case Pipeline(stages):
            return " | ".join(_shell_string(s) for s in stages)
        case _:
            assert_never(expr)


def describe(expr: ShellExpr) -> str:
    """The human-readable form of ``expr`` — for echo lines and test assertions.

    Display only: this is the logical command a human reads, not the argv that
    runs (which may wrap it in ``sh -c``). Use :func:`to_argv` to execute.
    """
    return _shell_string(expr)


def to_argv(expr: ShellExpr, *, replace_process: bool = False) -> list[str]:
    """The argv that executes ``expr``.

    A bare argv runs directly — no shell, so no quoting round-trip and no
    injection surface. A ``Command``/``Pipeline`` uses shell syntax, so it goes
    through ``sh -c``. With ``replace_process`` the shell string is prefixed with
    ``exec`` so the wrapping ``sh`` replaces itself with the target rather than
    lingering as a parent (a bare argv already replaces directly, so the flag is a
    no-op there).
    """
    match expr:
        case RawShell():
            # Raw text is shell syntax by definition, so it cannot bypass the
            # shell the way a structured argv does.
            s = str(expr)
            return ["sh", "-c", f"exec {s}" if replace_process else s]
        case list():  # bare argv — exec/run identically, no shell
            return expr
        case Command() | Pipeline():
            s = _shell_string(expr)
            return ["sh", "-c", f"exec {s}" if replace_process else s]
        case _:
            assert_never(expr)


async def _run_cmd_streams(
    cmd: ShellExpr,
    *,
    cwd: Path | None = None,
    env: dict | None = None,
    timeout: float | None = None,
) -> tuple[list[str], str, str, int]:
    """Spawn ``cmd`` on the caller's loop; return the argv it ran, its output and code.

    The subprocess mechanics of :func:`run_cmd_async`, kept separate only so
    that function reads as its contract rather than as its plumbing. Private:
    ``run_cmd_async`` is the one public async runner, and a second name that
    swallowed a non-zero exit is exactly the trap this split must not reopen.

    The argv comes back rather than being built twice, so the argv on the
    ``CompletedProcess`` is by construction the one that ran — the caller
    cannot pair a result with an argv the child never saw.
    """
    argv = to_argv(cmd)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env={**os.environ, **env} if env is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        _kill_group(proc)
        await proc.wait()
        raise subprocess.TimeoutExpired(describe(cmd), timeout or 0) from None
    return (
        argv,
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
        proc.returncode if proc.returncode is not None else -1,
    )


async def run_cmd_async(
    cmd: ShellExpr,
    *,
    cwd: Path | None = None,
    quiet: bool = False,
    check: bool = True,
    env: dict | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """:func:`run_cmd`'s contract, without blocking the calling thread.

    Same arguments, same :class:`~subprocess.CompletedProcess`, and the same
    ``CalledProcessError`` when ``check`` is set, so a caller converts by
    adding ``await``. Prefer this in anything a server awaits; :func:`run_cmd`
    stays the right call in a script, where blocking is what you want.

    There is no ``stream``: streaming interleaves output from every concurrent
    job, so a caller that wants it wants :func:`run_cmd`. Output is therefore
    always captured.

    ``timeout`` raises ``subprocess.TimeoutExpired``. The child gets its own
    process group, so a timeout kills a pipeline whole rather than leaving the
    stages behind their dead ``sh``.
    """
    _audit(cmd, cwd)
    if not quiet:
        _echo(cmd)
    argv, out, err, code = await _run_cmd_streams(
        cmd, cwd=cwd, env=env, timeout=timeout
    )
    result = subprocess.CompletedProcess(argv, code, out, err)
    if check:
        result.check_returncode()
    return result


def _kill_group(proc: "asyncio.subprocess.Process") -> None:
    """Kill ``proc``'s whole process group, falling back to the child alone."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        # The group went between the timeout and the kill.
        with suppress(ProcessLookupError):
            proc.kill()


def mael_path(env: Mapping[str, str] | None = None) -> str:
    """Absolute path to the ``mael`` binary, resolved against *env*'s ``PATH``.

    For spawning ``mael`` from a bare environment — a launchd job, or a daemon
    detached from the starting shell — where a bare ``mael`` may not resolve.
    Lives in ``shell`` because ``schedule_launchd`` is macOS-only.

    An inherited ``VIRTUAL_ENV`` is not allowed to decide the answer. ``mael``
    itself lives in ``_main/.venv/bin``, and a venv activation puts that
    directory first on ``PATH``, so every child resolves ``mael`` there
    whatever the user's own ``PATH`` says. The venv's ``bin`` is searched last
    instead of first, so it still answers when it is the only copy.

    Args:
        env: The environment to resolve against. Defaults to ``os.environ``.
    """
    env = os.environ if env is None else env
    path = env.get("PATH", "")
    virtual_env = env.get("VIRTUAL_ENV")
    if virtual_env:
        venv_bin = str(Path(virtual_env) / "bin")
        entries = [e for e in path.split(os.pathsep) if e and e != venv_bin]
        # Kept as a last resort: a machine whose only `mael` is the venv copy
        # still gets an answer rather than the conventional fallback below.
        path = os.pathsep.join([*entries, venv_bin])
    found = shutil.which("mael", path=path)
    if found:
        return found
    # Conventional location, for a PATH that does not carry `mael` yet.
    # `ensure_schedule_agent` rewrites the plist on the next install if wrong.
    return str(Path.home() / ".local" / "bin" / "mael")


#: Every command the chokepoint runs, for anything that wants an audit trail.
#: A module logger and not a caller-passed one: the point of a chokepoint is
#: that one handler sees every command, however deep the caller sits.
log = logging.getLogger(__name__)


def _audit(cmd: ShellExpr, cwd: Path | None) -> None:
    """Record one command on the audit log.

    ``RawShell`` is called out by name. A structured expression was built by
    this codebase; raw text came from outside it, and that difference is what
    an auditor is reading the log to find.
    """
    kind = "raw shell" if isinstance(cmd, RawShell) else "command"
    log.info("%s in %s: %s", kind, cwd or Path.cwd(), describe(cmd))


def _echo(cmd: ShellExpr) -> None:
    """Print the `$ cmd` line.

    Flushed: a streamed child writes to the shared stdout directly, and a
    block-buffered echo would land after the output it labels.
    """
    print(f"$ {describe(cmd)}", flush=True)


def exec_cmd(
    cmd: ShellExpr,
    *,
    cwd: Path | None = None,
    quiet: bool = False,
    env: dict | None = None,
) -> NoReturn:
    """Replace this process with a ``ShellExpr`` — the exec half of the chokepoint.

    Never returns. ``to_argv`` prefixes ``exec`` so a wrapping ``sh`` replaces
    itself and nothing lingers.

    This is deliberately separate from :func:`run_cmd`. An exec has no result to
    check, no output to capture or stream, and no wait to time out, so the
    options that describe those are absent here rather than silently ignored.

    If ``env`` is provided, its keys are merged over the current process
    environment (``os.environ``) rather than replacing it wholesale.
    """
    _audit(cmd, cwd)
    if not quiet:
        _echo(cmd)
    if cwd is not None:
        os.chdir(cwd)
    if env:
        os.environ.update(env)
    argv = to_argv(cmd, replace_process=True)
    os.execvp(argv[0], argv)  # NoReturn


def run_cmd(
    cmd: ShellExpr,
    *,
    cwd: Path | None = None,
    quiet: bool = False,
    check: bool = True,
    stream: bool = False,
    env: dict | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Run a ``ShellExpr`` and wait for it — the fork-and-wait half of the chokepoint.

    The shell-vs-no-shell decision lives in :func:`to_argv`: a bare argv runs
    directly with ``shell=False`` (the ~30 git sites stay byte-identical), a
    ``Command``/``Pipeline`` goes through ``sh -c``.

    Use :func:`exec_cmd` to replace this process instead of forking.

    ``stream=True`` sends the child's output to this process's stdout as it runs,
    rather than capturing it, so ``stdout``/``stderr`` on the result are empty.

    If ``env`` is provided, its keys are merged over the current process
    environment (``os.environ``) rather than replacing it wholesale.

    ``timeout`` bounds the wait in seconds and raises
    ``subprocess.TimeoutExpired`` when it passes; ``None`` waits forever. It
    applies to a streamed command as much as a captured one, so a long-running
    child can be watched and still be given a deadline.
    """
    _audit(cmd, cwd)
    if not quiet:
        _echo(cmd)
    merged_env = {**os.environ, **env} if env is not None else None
    return subprocess.run(
        to_argv(cmd),
        cwd=cwd,
        capture_output=not stream,
        text=True,
        check=check,
        env=merged_env,
        timeout=timeout,
    )
