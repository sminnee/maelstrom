"""``ensure_fetched``: one fetch per project, however many callers ask at once.

The remote counts its own contacts. ``remote.origin.uploadpack`` names a script
that appends a line to a counter file and then runs the real upload-pack, so a
test reads what the remote saw rather than spying on argv.
"""

import os
import subprocess
import threading
import time
from pathlib import Path

import pytest
from git_helpers import run_git

from mael_domain import worktree
from mael_domain.worktree import ensure_fetched, rebase_worktree
from mael_domain.worktree_steps import Scope, scope_lock


def _upload_pack(project_path: Path, body: str) -> None:
    """Make every contact with origin run ``body`` as its upload-pack."""
    script = project_path.parent / "upload-pack.sh"
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(0o755)
    run_git(project_path, "config", "remote.origin.uploadpack", str(script))


def _counting_remote(project_path: Path, delay: float = 0.0) -> Path:
    """Count origin's contacts; ``delay`` holds each one open that long."""
    counter = project_path.parent / "contacts"
    counter.write_text("")
    _upload_pack(
        project_path,
        f'echo x >> "{counter}"\nsleep {delay}\nexec git upload-pack "$@"',
    )
    return counter


def _contacts(counter: Path) -> int:
    return len(counter.read_text().splitlines())


def _fetch_head(project_path: Path) -> Path:
    return project_path / ".git" / "FETCH_HEAD"


def _age_stamp(project_path: Path) -> None:
    """Make the last full fetch an hour old."""
    old = time.time() - 3600
    os.utime(_fetch_head(project_path), (old, old))


def test_concurrent_callers_share_one_fetch(project_with_worktree):
    project_path, _, _ = project_with_worktree
    _age_stamp(project_path)
    # Each contact is held open, so both callers read the stale stamp before
    # either fetch ends. Only the re-check under the lock can then skip one.
    counter = _counting_remote(project_path, delay=0.5)
    start = threading.Barrier(2)
    errors: list[BaseException] = []

    def call() -> None:
        start.wait()
        try:
            ensure_fetched(project_path, max_age=60)
        except BaseException as e:  # noqa: BLE001  (re-raised by the assert)
            errors.append(e)

    threads = [threading.Thread(target=call) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert _contacts(counter) == 1


@pytest.mark.usefixtures("stamp_honoured")
def test_a_fresh_stamp_skips_the_fetch(project_with_worktree):
    project_path, _, _ = project_with_worktree
    os.utime(_fetch_head(project_path))
    counter = _counting_remote(project_path)

    assert ensure_fetched(project_path) is None
    assert _contacts(counter) == 0

    assert ensure_fetched(project_path, max_age=0) is not None
    assert _contacts(counter) == 1


def test_a_narrow_fetch_leaves_the_stamp_alone(project_with_worktree):
    project_path, _, _ = project_with_worktree
    _age_stamp(project_path)
    before = _fetch_head(project_path).stat().st_mtime
    counter = _counting_remote(project_path)

    ensure_fetched(project_path, refs=["main"])

    assert _contacts(counter) == 1
    assert _fetch_head(project_path).stat().st_mtime == before


def test_a_hung_remote_times_out_and_frees_the_repo_scope(
    project_with_worktree, monkeypatch
):
    project_path, _, _ = project_with_worktree
    monkeypatch.setattr(worktree, "FETCH_TIMEOUT", 1.0)
    # stderr away from the fetch's capture pipe, or the orphaned sleep keeps
    # it open and the call waits for the sleep after the kill.
    _upload_pack(project_path, "exec sleep 3 2>/dev/null")

    with pytest.raises(subprocess.TimeoutExpired):
        ensure_fetched(project_path)

    with scope_lock(project_path, Scope.REPO, None, timeout=0.2):
        pass


def test_a_hung_fetch_fails_the_rebase(project_with_worktree, monkeypatch):
    _, worktree_path, _ = project_with_worktree
    monkeypatch.setattr(worktree, "FETCH_TIMEOUT", 1.0)
    _upload_pack(worktree_path.parent, "exec sleep 3 2>/dev/null")

    result = rebase_worktree(worktree_path)

    assert result.success is False
    assert "timed out" in result.message
