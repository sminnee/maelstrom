"""Closed command algebra: a small, auditable shell-expression grammar.

A ``ShellExpr`` is a closed union; the supported shell-feature set is deliberately
small and fixed — adding a node means editing this union AND the two matches below,
so the surface stays reviewable. ``list[str]`` (a bare argv) is a first-class member.

Three public entry points, all owned here so callers stay shell-agnostic:

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

``run_cmd`` and ``exec_cmd`` together are the execution chokepoint: every command
in the codebase routes through one of them, so they are the seam to mock / log /
intercept.

Quoting/escaping happens in exactly one place: ``_shell_string``. This module is
a leaf — it imports only stdlib, so anything may depend on it without cycles.
"""

import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn, assert_never


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


# Closed union. ``list[str]`` is a literal member (the zero-ceremony base case).
ShellExpr = list[str] | Command | Pipeline


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
        case list():  # bare argv — exec/run identically, no shell
            return expr
        case Command() | Pipeline():
            s = _shell_string(expr)
            return ["sh", "-c", f"exec {s}" if replace_process else s]
        case _:
            assert_never(expr)


def _echo(cmd: ShellExpr) -> None:
    """Print the `$ cmd` line.

    Flushed: a streamed child writes to the shared stdout directly, and a
    block-buffered echo would land after the output it labels.
    """
    print(f"$ {describe(cmd)}", flush=True)


def exec_cmd(cmd: ShellExpr, *, cwd: Path | None = None, quiet: bool = False, env: dict | None = None) -> NoReturn:
    """Replace this process with a ``ShellExpr`` — the exec half of the chokepoint.

    Never returns. ``to_argv`` prefixes ``exec`` so a wrapping ``sh`` replaces
    itself and nothing lingers.

    This is deliberately separate from :func:`run_cmd`. An exec has no result to
    check, no output to capture or stream, and no wait to time out, so the
    options that describe those are absent here rather than silently ignored.

    If ``env`` is provided, its keys are merged over the current process
    environment (``os.environ``) rather than replacing it wholesale.
    """
    if not quiet:
        _echo(cmd)
    if cwd is not None:
        os.chdir(cwd)
    if env:
        os.environ.update(env)
    argv = to_argv(cmd, replace_process=True)
    os.execvp(argv[0], argv)  # NoReturn


def run_cmd(cmd: ShellExpr, *, cwd: Path | None = None, quiet: bool = False, check: bool = True, stream: bool = False, env: dict | None = None, timeout: float | None = None) -> subprocess.CompletedProcess:
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
