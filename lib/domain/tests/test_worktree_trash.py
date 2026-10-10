"""Tests for the trash sequence behind ``mael close --trash`` and the server.

The seam is :func:`mael_domain.worktree_trash.trash_worktree_fully`: what it
does, in what order, and what it refuses. The real-repo tests drive the real
git steps; the rest stub every collaborator, as ``test_worktree_close.py`` does.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from git_helpers import create_commit, run_git

from mael_domain.base_store import GitConfigBaseStore
from mael_domain.github_model import GitHubCommandFailed, PrStatus
from mael_domain.worktree import CloseResult
from mael_domain.worktree_model import BaseRef, CopyBackResult
from mael_domain.worktree_steps import StepHook
from mael_domain.worktree_trash import TrashSteps, trash_worktree_fully

PR = PrStatus(number=7, commits=1, url="", state="unknown", is_draft=False)


async def _no_agents(path):
    return []


async def _no_pr(cwd, branch):
    return None


def real_git(**over) -> TrashSteps:
    """The real git steps, with cmux, the daemon, the env and gh stubbed out."""
    defaults = dict(
        env_status=lambda project, worktree: None,
        stop_env=lambda project, worktree: [],
        stop_agents=_no_agents,
        live_sessions=lambda path: [],
        stop_sessions=lambda sessions: [],
        copy_back=lambda project_path, path: CopyBackResult(),
        close_workspace=lambda project, worktree: False,
        find_pr=_no_pr,
        close_pr=lambda cwd, number, comment: None,
    )
    return TrashSteps(**{**defaults, **over})


def _refs(repo: Path, pattern: str) -> list[str]:
    out = run_git(repo, "for-each-ref", "--format=%(refname)", pattern).stdout
    return [line for line in out.splitlines() if line]


def _branch_of(worktree_path: Path) -> str:
    return run_git(worktree_path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def _pushed_branch(project_path: Path, worktree_path: Path) -> None:
    """``feature/work`` with a commit, pushed, a base, and a working history."""
    create_commit(worktree_path, "work.txt", "work\n", "feat: work")
    run_git(worktree_path, "push", "-u", "origin", "feature/work")
    GitConfigBaseStore(project_path).write(
        "feature/work", BaseRef(branch="feature/base", tip=None)
    )
    run_git(
        project_path,
        "update-ref",
        "refs/mael/history/feature/work/20260101T000000Z",
        "feature/work",
    )


async def _trash_alpha(project_path: Path, worktree_path: Path, **over):
    outcome = await trash_worktree_fully(
        "test-repo", "alpha", worktree_path, project_path, steps=real_git(**over)
    )
    return outcome.close


class TestAgainstARealRepo:
    async def test_a_worktree_is_trashed_with_its_uncommitted_work(
        self, project_with_worktree
    ):
        project_path, worktree_path, remote_path = project_with_worktree
        _pushed_branch(project_path, worktree_path)
        (worktree_path / "draft.txt").write_text("unsaved\n")

        result = await _trash_alpha(project_path, worktree_path)

        assert result.success, result.message
        assert result.message == "Trashed feature/work as trash/feature/work."
        assert _refs(project_path, "refs/heads/") == [
            "refs/heads/main",
            "refs/heads/trash/feature/work",
        ]
        assert _refs(remote_path, "refs/heads/") == [
            "refs/heads/main",
            "refs/heads/trash/feature/work",
        ]
        upstream = run_git(
            project_path, "config", "branch.trash/feature/work.merge"
        ).stdout.strip()
        assert upstream == "refs/heads/trash/feature/work"
        # The dirty file rode along in a wip commit.
        log = run_git(project_path, "log", "-1", "--format=%s", "trash/feature/work")
        assert log.stdout.strip() == "wip: uncommitted changes"
        files = run_git(
            project_path, "show", "--name-only", "--format=", "trash/feature/work"
        ).stdout.split()
        assert files == ["draft.txt"]
        # The history moved, and the base went.
        assert _refs(project_path, "refs/mael/history/") == [
            "refs/mael/history/trash/feature/work/20260101T000000Z"
        ]
        assert GitConfigBaseStore(project_path).all() == {}
        # The worktree is an empty slot.
        assert _branch_of(worktree_path) == "HEAD"

    async def test_a_branch_never_pushed_is_trashed_to_origin(
        self, project_with_worktree
    ):
        project_path, worktree_path, remote_path = project_with_worktree
        create_commit(worktree_path, "work.txt", "work\n", "feat: work")

        result = await _trash_alpha(project_path, worktree_path)

        assert result.success, result.message
        assert "refs/heads/trash/feature/work" in _refs(remote_path, "refs/heads/")
        assert _refs(project_path, "refs/heads/") == [
            "refs/heads/main",
            "refs/heads/trash/feature/work",
        ]

    async def test_an_open_pr_is_closed_while_its_branch_is_on_origin(
        self, project_with_worktree
    ):
        project_path, worktree_path, remote_path = project_with_worktree
        _pushed_branch(project_path, worktree_path)
        seen: list[tuple[int, str, list[str]]] = []

        async def find_pr(cwd, branch):
            return PR

        def close_pr(cwd, number, comment):
            seen.append((number, comment, _refs(remote_path, "refs/heads/")))

        result = await _trash_alpha(
            project_path, worktree_path, find_pr=find_pr, close_pr=close_pr
        )

        assert result.success, result.message
        [(number, comment, heads)] = seen
        assert number == 7
        assert comment == "Trashed: branch moved to trash/feature/work"
        assert "refs/heads/feature/work" in heads

    async def test_a_failed_push_leaves_the_worktree_ready_to_try_again(
        self, project_with_worktree
    ):
        project_path, worktree_path, remote_path = project_with_worktree
        _pushed_branch(project_path, worktree_path)
        run_git(project_path, "remote", "set-url", "origin", "/nowhere/remote.git")

        failed = await _trash_alpha(project_path, worktree_path)

        assert not failed.success
        assert "Could not move feature/work on origin" in failed.message
        assert _branch_of(worktree_path) == "feature/work"

        run_git(project_path, "remote", "set-url", "origin", str(remote_path))
        retried = await _trash_alpha(project_path, worktree_path)

        assert retried.success, retried.message
        assert _refs(remote_path, "refs/heads/") == [
            "refs/heads/main",
            "refs/heads/trash/feature/work",
        ]

    async def test_a_trash_that_stopped_after_its_push_can_finish(
        self, project_with_worktree
    ):
        project_path, worktree_path, remote_path = project_with_worktree
        _pushed_branch(project_path, worktree_path)
        run_git(project_path, "push", "origin", "feature/work:trash/feature/work")

        result = await _trash_alpha(project_path, worktree_path)

        assert result.success, result.message
        assert "refs/heads/feature/work" not in _refs(remote_path, "refs/heads/")

    async def test_commits_only_origin_holds_are_not_deleted(
        self, project_with_worktree
    ):
        project_path, worktree_path, remote_path = project_with_worktree
        _pushed_branch(project_path, worktree_path)
        run_git(worktree_path, "reset", "--hard", "HEAD~1")

        result = await _trash_alpha(project_path, worktree_path)

        assert not result.success
        assert "origin/feature/work has commits" in result.message
        assert "refs/heads/feature/work" in _refs(remote_path, "refs/heads/")

    @pytest.mark.parametrize(
        ("branch", "refusal"),
        [
            ("main", "main cannot be trashed"),
            ("trash/old", "trash/old is already in the trash"),
            ("feature/taken", "trash/feature/taken already exists"),
            ("feature/remote", "trash/feature/remote already exists on origin"),
            ("feature/base", "Other branches are stacked on feature/base"),
        ],
    )
    async def test_a_branch_that_cannot_be_trashed_is_refused(
        self, project_with_worktree, branch, refusal
    ):
        project_path, worktree_path, _ = project_with_worktree
        create_commit(worktree_path, "work.txt", "work\n", "feat: work")
        for name in (
            "trash/old",
            "feature/taken",
            "trash/feature/taken",
            "feature/remote",
            "feature/base",
        ):
            run_git(project_path, "branch", name, "main")
        run_git(project_path, "push", "origin", "feature/work:trash/feature/remote")
        GitConfigBaseStore(project_path).write(
            "feature/child", BaseRef(branch="feature/base", tip=None)
        )
        before = _refs(project_path, "refs/heads/")

        result = await _trash_alpha(
            project_path, worktree_path, current_branch=lambda path: branch
        )

        assert not result.success
        assert refusal in result.message
        assert _refs(project_path, "refs/heads/") == before
        assert _branch_of(worktree_path) == "feature/work"

    async def test_a_closed_worktree_has_nothing_to_trash(self, project_with_worktree):
        project_path, worktree_path, _ = project_with_worktree
        run_git(worktree_path, "checkout", "--detach")

        result = await _trash_alpha(project_path, worktree_path)

        assert not result.success
        assert "not on a branch" in result.message


WORKTREE_PATH = Path("/Users/dev/Projects/myproject/myproject-alpha")
PROJECT_PATH = Path("/Users/dev/Projects/myproject")


def recording(order: list[str], **over) -> TrashSteps:
    """Every collaborator stubbed to succeed and note that it ran."""

    def note(name, value=None):
        return lambda *args: order.append(name) or value

    async def stop_agents(path):
        order.append("stop_agents")
        return []

    async def find_pr(cwd, branch):
        order.append("find_pr")
        return PR

    defaults = dict(
        env_status=lambda project, worktree: [MagicMock(alive=True)],
        stop_env=note("stop_env", []),
        stop_agents=stop_agents,
        live_sessions=lambda path: [MagicMock()],
        stop_sessions=note("stop_sessions", []),
        copy_back=note("copy_back", CopyBackResult()),
        close_workspace=note("close_workspace", True),
        current_branch=lambda path: "feature/work",
        refusal=note("guard"),
        commit_wip=note("commit_wip", False),
        detach=note("detach", CloseResult(success=True, message="Detached")),
        find_pr=find_pr,
        close_pr=note("close_pr"),
        rename_remote=note("rename_remote"),
        rename=note("rename"),
    )
    return TrashSteps(**{**defaults, **over})


class Planned(StepHook):
    """A hook that notes the plan it hears, and skips the steps it is told to."""

    def __init__(self, skip: tuple[str, ...] = ()) -> None:
        self.names: list[str] = []
        self.skip = skip

    def planned(self, names: list[str]) -> None:
        self.names = names

    def skips(self, name: str) -> bool:
        return name in self.skip


async def trash_fully(order, **over):
    return await trash_worktree_fully(
        "myproject",
        "alpha",
        WORKTREE_PATH,
        PROJECT_PATH,
        steps=recording(order, **over),
    )


async def trash(order, **over):
    return (await trash_fully(order, **over)).close


def _raises(error):
    def refuse(*args):
        raise error

    return refuse


#: What has run when a network step refuses: nothing that takes the worktree
#: off its branch.
BEFORE_THE_PR = [
    "guard",
    "stop_env",
    "stop_agents",
    "stop_sessions",
    "copy_back",
    "commit_wip",
]


class TestTheSequence:
    async def test_the_network_steps_run_before_the_worktree_leaves_its_branch(self):
        order: list[str] = []
        result = await trash(order)
        assert result.success, result.message
        assert order == [
            *BEFORE_THE_PR,
            "find_pr",
            "close_pr",
            "rename_remote",
            "detach",
            "rename",
            "close_workspace",
        ]

    async def test_a_refusal_stops_nothing_and_changes_nothing(self):
        order: list[str] = []
        result = await trash(order, refusal=lambda p, b: "feature/work is busy")
        assert not result.success
        assert result.message == "feature/work is busy"
        assert order == []

    async def test_a_pr_lookup_that_fails_is_not_read_as_no_pr(self):
        order: list[str] = []

        async def find_pr(cwd, branch):
            raise GitHubCommandFailed("look up the PR", "HTTP 502")

        result = await trash(order, find_pr=find_pr)
        assert not result.success
        assert "HTTP 502" in result.message
        assert order == BEFORE_THE_PR

    async def test_a_pr_that_will_not_close_keeps_the_branch_on_origin(self):
        order: list[str] = []
        refused = GitHubCommandFailed("close PR #7", "forbidden")
        result = await trash(order, close_pr=_raises(refused))
        assert not result.success
        assert "forbidden" in result.message
        assert order == [*BEFORE_THE_PR, "find_pr"]

    async def test_a_failed_push_keeps_the_worktree_and_the_workspace(self):
        order: list[str] = []
        rejected = subprocess.CalledProcessError(1, "git", stderr="rejected")
        result = await trash(order, rename_remote=_raises(rejected))
        assert not result.success
        assert "rejected" in result.message
        assert order == [*BEFORE_THE_PR, "find_pr", "close_pr"]

    async def test_a_commit_that_fails_is_a_refusal_not_a_crash(self):
        order: list[str] = []
        hook = subprocess.CalledProcessError(1, "git", stderr="pre-commit failed")
        result = await trash(order, commit_wip=_raises(hook))
        assert not result.success
        assert "pre-commit failed" in result.message
        assert "find_pr" not in order

    async def test_the_env_rescue_is_reported(self):
        rescued = CopyBackResult(added={"NEW_KEY": "1"})
        result = await trash_fully(
            [],
            copy_back=lambda project_path, path: rescued,
            commit_wip=lambda path: True,
        )
        assert result.copy_back is rescued
        # The lines said before the rescue, so a caller reports it in place.
        split = result.messages_before_copy_back
        assert result.messages[:split] == [
            "Stopping environment for 'alpha'...",
            "Stopping 1 Claude session(s) in 'alpha'...",
        ]
        assert result.messages[split] == "Committed uncommitted changes as wip."

    async def test_the_main_checkout_is_refused(self):
        order: list[str] = []
        result = await trash_worktree_fully(
            "myproject",
            "_main",
            PROJECT_PATH / "_main",
            PROJECT_PATH,
            steps=recording(order),
        )
        assert not result.close.success
        assert order == []


async def test_the_hook_hears_the_trash_s_steps():
    hook = Planned()
    await trash_worktree_fully(
        "myproject",
        "alpha",
        WORKTREE_PATH,
        PROJECT_PATH,
        steps=recording([]),
        hook=hook,
    )
    assert hook.names == [
        "guard",
        "stop_env",
        "stop_agents",
        "stop_sessions",
        "rescue_env_vars",
        "commit_wip",
        "close_pr",
        "rename_remote",
        "detach",
        "rename",
        "close_workspace",
    ]
