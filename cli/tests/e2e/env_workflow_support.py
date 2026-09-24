"""Helpers for the env workflow's end-to-end tests."""

import os
import time


def wait_for(predicate, timeout=5.0, interval=0.1):
    """Poll predicate until truthy or raise TimeoutError."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    raise TimeoutError(f"Condition not met within {timeout}s")


def assert_process_dead(pid, timeout=5.0):
    """Assert a process is dead, reaping zombies as needed.

    After SIGKILL, child processes become zombies until reaped.
    This helper polls with os.waitpid to reap them.
    """
    from mael_domain.env import is_service_alive

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # Try to reap the zombie child
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        if not is_service_alive(pid):
            return
        time.sleep(0.1)
    raise AssertionError(f"Process {pid} still alive after {timeout}s")


def write_procfile(worktree_path, services):
    """Write a Procfile from a dict of {name: command}."""
    lines = [f"{name}: {cmd}" for name, cmd in services.items()]
    (worktree_path / "Procfile").write_text("\n".join(lines) + "\n")
