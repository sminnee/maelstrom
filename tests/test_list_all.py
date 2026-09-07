"""``build_list_all_data`` on a real project layout.

The rows feed both ``mael list-all`` and the orchestrator server, so they are
checked here once, against the bare-clone-plus-worktree fixture.
"""

import asyncio
import dataclasses
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from maelstrom.github_model import PrStatus
from maelstrom.list_all import (
    build_list_all_data,
    project_repo_url,
    repo_url_from_remote,
)
from maelstrom.worktree import WorktreeInfo, list_worktrees, run_git
from tests.test_sync_flags import project_with_worktree  # noqa: F401  (fixture)


def _pr(number, *, commits=1, state="ready"):
    """A `PrStatus` for a row that only cares which PR it is."""
    return PrStatus(
        number=number,
        commits=commits,
        url=f"https://github.com/acme/repo/pull/{number}",
        state=state,
        is_draft=False,
    )


def test_build_list_all_data_reads_the_project_and_its_worktree(
    project_with_worktree,  # noqa: F811
):
    project_path, worktree_path, _remote = project_with_worktree
    (project_path / ".mael").touch()
    with (
        patch("maelstrom.list_all.get_open_prs_async", return_value={}),
        patch("maelstrom.session_discovery.LiveSessionSet.count_for", return_value=0),
    ):
        data = asyncio.run(build_list_all_data(project_path.parent))

    assert [p["name"] for p in data["projects"]] == ["test-repo"]
    project = data["projects"][0]
    assert project["path"] == str(project_path)
    assert project["stack_tip"] == "main"
    assert len(project["worktrees"]) == 1
    row = project["worktrees"][0]
    assert row["name"] == "alpha"
    assert row["folder"] == "test-repo-alpha"
    assert Path(row["path"]).resolve() == worktree_path.resolve()
    assert row["branch"] == "feature/work"
    assert row["is_closed"] is False
    assert row["dirty_files"] == 0
    assert row["pr_number"] is None
    assert row["session_count"] == 0
    assert row["session_stopped"] is False


@pytest.mark.parametrize(
    "remote,expected",
    [
        ("https://github.com/acme/test-repo.git", "https://github.com/acme/test-repo"),
        ("git@github.com:acme/test-repo.git", "https://github.com/acme/test-repo"),
        ("https://github.com/acme/test-repo", "https://github.com/acme/test-repo"),
        # A GitHub Enterprise remote keeps its own host.
        ("git@ghe.acme.io:acme/test-repo.git", "https://ghe.acme.io/acme/test-repo"),
        # Not a git host we can build a browse URL for.
        ("/srv/mirrors/test-repo.git", None),
        ("", None),
    ],
)
def test_repo_url_from_remote_reads_the_browse_url(remote, expected):
    """`git config` answers this, so no project pays a network call per poll."""
    assert repo_url_from_remote(remote) == expected


def test_the_project_row_carries_its_repo_url(
    project_with_worktree,  # noqa: F811
):
    """The row carries the browse URL. The card joins it with a PR number."""
    project_path, _worktree_path, _remote = project_with_worktree
    (project_path / ".mael").touch()
    # The fixture's origin is a local bare path, which has no browse URL.
    run_git(
        ["config", "remote.origin.url", "git@github.com:test/test-repo.git"],
        cwd=project_path,
    )
    with (
        patch("maelstrom.list_all.get_open_prs_async", return_value={}),
        patch("maelstrom.session_discovery.LiveSessionSet.count_for", return_value=0),
    ):
        data = asyncio.run(build_list_all_data(project_path.parent))

    assert data["projects"][0]["repo_url"] == "https://github.com/test/test-repo"


def _row_for(project_path, pr):
    """The one worktree row `build_list_all_data` makes when the PR batch
    answers ``pr`` for the worktree's branch.

    The batch is what is patched, not `resolve_pr`: the seam under test is the
    whole path from a batch answer to a row, and patching `resolve_pr` would
    step over the wiring the row depends on. `get_open_prs` keys on the branch,
    so the batch is built after the branch is known.
    """
    branches = {wt.branch for wt in list_worktrees(project_path) if wt.branch}
    batch = {branch: pr for branch in branches} if pr else {}
    with (
        patch("maelstrom.list_all.get_open_prs_async", return_value=batch),
        patch("maelstrom.session_discovery.LiveSessionSet.count_for", return_value=0),
    ):
        data = asyncio.run(build_list_all_data(project_path.parent))
    return data["projects"][0]["worktrees"][0]


def test_a_worktree_row_carries_its_prs_own_url(
    project_with_worktree,  # noqa: F811
):
    """The row carries the PR URL, so no reader joins a project field to a row."""
    project_path, _worktree_path, _remote = project_with_worktree
    (project_path / ".mael").touch()
    row = _row_for(project_path, _pr(42, commits=3))
    assert row["pr_url"] == "https://github.com/acme/repo/pull/42"


def test_a_pr_with_no_url_of_its_own_falls_back_to_the_repo_join(
    project_with_worktree,  # noqa: F811
):
    """The per-branch fallback lookup answers a number but no URL. A row must
    still link, rather than showing a PR the reader cannot open."""
    project_path, _worktree_path, _remote = project_with_worktree
    (project_path / ".mael").touch()
    run_git(
        ["config", "remote.origin.url", "git@github.com:test/test-repo.git"],
        cwd=project_path,
    )
    row = _row_for(project_path, dataclasses.replace(_pr(42), url=""))
    assert row["pr_url"] == "https://github.com/test/test-repo/pull/42"


def test_a_worktree_row_carries_its_pr_state(
    project_with_worktree,  # noqa: F811
):
    """The state is decided once, in Python, so no reader re-derives it."""
    project_path, _worktree_path, _remote = project_with_worktree
    (project_path / ".mael").touch()
    row = _row_for(project_path, _pr(42, state="ci-running"))
    assert (row["pr_state"], row["pr_draft"]) == ("ci-running", False)


def test_a_merged_pr_still_counts_the_pushed_commits(
    project_with_worktree,  # noqa: F811
):
    """A merged PR means the branch needs a new one, so the row must keep the
    pushed count that says how much is waiting. Only an open PR replaces it."""
    project_path, _worktree_path, _remote = project_with_worktree
    (project_path / ".mael").touch()
    with patch("maelstrom.list_all.get_pushed_commit_count_async", return_value=4):
        row = _row_for(project_path, _pr(42, state="merged"))
    assert row["pushed_commits"] == 4


def test_an_open_pr_replaces_the_pushed_commit_count(
    project_with_worktree,  # noqa: F811
):
    project_path, _worktree_path, _remote = project_with_worktree
    (project_path / ".mael").touch()
    with patch("maelstrom.list_all.get_pushed_commit_count_async", return_value=4):
        row = _row_for(project_path, _pr(42, state="ready"))
    assert row["pushed_commits"] is None


def test_a_worktree_row_with_no_pr_carries_no_state(
    project_with_worktree,  # noqa: F811
):
    project_path, _worktree_path, _remote = project_with_worktree
    (project_path / ".mael").touch()
    row = _row_for(project_path, None)
    assert (row["pr_state"], row["pr_draft"]) == (None, None)


def test_a_worktree_row_with_no_pr_carries_no_pr_url(
    project_with_worktree,  # noqa: F811
):
    """No PR, and no browse URL, both mean no link. Neither is a URL to nowhere."""
    project_path, _worktree_path, _remote = project_with_worktree
    (project_path / ".mael").touch()
    run_git(
        ["config", "remote.origin.url", "git@github.com:test/test-repo.git"],
        cwd=project_path,
    )
    with (
        patch("maelstrom.list_all.get_open_prs_async", return_value={}),
        patch("maelstrom.session_discovery.LiveSessionSet.count_for", return_value=0),
    ):
        data = asyncio.run(build_list_all_data(project_path.parent))

    assert data["projects"][0]["worktrees"][0]["pr_url"] is None


def test_a_project_dir_that_is_not_a_git_repo_carries_no_repo_url(tmp_path):
    """`list-all` visits every project dir. A non-repo must not break the read."""
    project_path = tmp_path / "test-repo"
    project_path.mkdir()
    (project_path / ".mael").touch()
    with (
        patch("maelstrom.list_all.get_open_prs_async", return_value={}),
        patch("maelstrom.session_discovery.LiveSessionSet.count_for", return_value=0),
    ):
        data = asyncio.run(build_list_all_data(tmp_path))

    assert data["projects"][0]["repo_url"] is None


def test_a_project_path_that_does_not_exist_carries_no_repo_url(tmp_path):
    """A project dir can vanish between the scan and the read. That is not a crash."""
    missing = tmp_path / "gone"
    assert asyncio.run(project_repo_url(missing)) is None


def test_a_project_with_no_remote_carries_no_repo_url(tmp_path, monkeypatch):
    """A project with no origin must still list; the card then draws no PR link."""
    project_path = tmp_path / "test-repo"
    project_path.mkdir()
    (project_path / ".mael").touch()
    run_git(["init", "-q", str(project_path)])
    with (
        patch("maelstrom.list_all.get_open_prs_async", return_value={}),
        patch("maelstrom.session_discovery.LiveSessionSet.count_for", return_value=0),
    ):
        data = asyncio.run(build_list_all_data(tmp_path))

    assert data["projects"][0]["repo_url"] is None


def test_a_projects_dir_with_no_projects_is_empty(tmp_path):
    assert asyncio.run(build_list_all_data(tmp_path)) == {"projects": []}


def test_the_project_root_is_excluded_under_a_symlinked_projects_dir(
    project_with_worktree,  # noqa: F811
    tmp_path,
):
    """git reports real paths, so the root must be matched by resolved path."""
    project_path, _worktree_path, _remote = project_with_worktree
    (project_path / ".mael").touch()
    link = tmp_path / "link"
    link.symlink_to(project_path.parent)
    with (
        patch("maelstrom.list_all.get_open_prs_async", return_value={}),
        patch("maelstrom.session_discovery.LiveSessionSet.count_for", return_value=0),
    ):
        data = asyncio.run(build_list_all_data(link))
    assert [row["name"] for row in data["projects"][0]["worktrees"]] == ["alpha"]


def test_the_projects_are_read_at_the_same_time(tmp_path):
    """Each project's reads are independent, so they must not queue.

    Read one after another, a machine with 16 projects spent 19s in the
    worktree poll. Every route waits on the first read, so that time was
    dead air for the whole UI.
    """
    for name in ("alpha", "bravo", "charlie", "delta"):
        (tmp_path / name / ".mael").mkdir(parents=True)

    running = 0
    overlapped = False

    async def slow_list_worktrees(project_path):
        nonlocal running, overlapped
        running += 1
        overlapped = overlapped or running > 1
        await asyncio.sleep(0.05)
        running -= 1
        return []

    with patch("maelstrom.list_all.list_worktrees_async", slow_list_worktrees):
        data = asyncio.run(build_list_all_data(tmp_path))

    assert len(data["projects"]) == 4
    assert overlapped, "the projects were read one after another"


def test_one_unreadable_project_does_not_blank_the_others(tmp_path):
    """A project that cannot be read costs its own row, not the whole read.

    The reads run together, so an unguarded raise from one aborts the gather
    and the poll returns nothing. Every 15s tick would then blank the UI, and
    a worktree directory can go between the listing and the row read.
    """
    for name in ("alpha", "bravo", "charlie"):
        (tmp_path / name / ".mael").mkdir(parents=True)

    async def one_project_is_gone(project_path):
        if project_path.name == "bravo":
            raise FileNotFoundError(project_path)
        return []

    with patch("maelstrom.list_all.list_worktrees_async", one_project_is_gone):
        data = asyncio.run(build_list_all_data(tmp_path))

    assert [p["name"] for p in data["projects"]] == ["alpha", "charlie"]


def _fake_worktrees(project_path: Path, count: int) -> list[WorktreeInfo]:
    """``count`` open worktrees under ``project_path``, as git would report them."""
    return [
        WorktreeInfo(
            path=project_path / f"{project_path.name}-{n}",
            branch=f"branch-{project_path.name}-{n}",
            commit="deadbeef",
        )
        for n in range(count)
    ]


@contextmanager
def _quiet_worktree_reads(**overrides):
    """Stop a fake worktree row reaching git, ``gh`` or the ports file.

    ``overrides`` names the read the test is actually about, so each test
    patches one function itself and lets the rest answer nothing.
    """
    quiet = {
        "get_open_prs_async": {},
        "closed_worktrees_async": set(),
        "project_repo_url": None,
        "get_worktree_dirty_files_async": [],
        "get_local_only_commits_async": 0,
        "get_pushed_commit_count_async": 0,
        "get_app_url": None,
        "branch_session_ids": {},
    }
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "maelstrom.session_discovery.LiveSessionSet.count_for", return_value=0
            )
        )
        for name in {**quiet, **overrides}:
            target = f"maelstrom.list_all.{name}"
            if name in overrides:
                stack.enter_context(patch(target, overrides[name]))
            else:
                stack.enter_context(patch(target, return_value=quiet[name]))
        yield


class _ConcurrencyProbe:
    """Records how many patched reads overlap, and the highest number seen.

    One probe can stand in for several reads at once, so a test can count a
    project-level read and a worktree-level one against the same budget.
    :meth:`answering` fixes what a given read hands back.
    """

    def __init__(self, delay=0.02):
        self.running = 0
        self.peak = 0
        self.delay = delay

    def answering(self, value):
        """A read that answers ``value``, counted like every other."""

        async def read(*args, **kwargs):
            self.running += 1
            self.peak = max(self.peak, self.running)
            await asyncio.sleep(self.delay)
            self.running -= 1
            return value

        return read

    @property
    def read(self):
        """A read that answers an empty list — the common case."""
        return self.answering([])


def test_the_worktrees_of_a_project_are_read_at_the_same_time(tmp_path):
    """A project's worktree rows are independent, so they must not queue.

    The projects already read together, so the read is now bounded by the
    slowest single project. A user who adds worktrees to one project would
    otherwise see the whole poll slow down.
    """
    (tmp_path / "alpha" / ".mael").mkdir(parents=True)
    probe = _ConcurrencyProbe()

    async def four_worktrees(project_path):
        return _fake_worktrees(project_path, 4)

    with _quiet_worktree_reads(
        list_worktrees_async=four_worktrees,
        get_worktree_dirty_files_async=probe.read,
    ):
        data = asyncio.run(build_list_all_data(tmp_path))

    assert len(data["projects"][0]["worktrees"]) == 4
    assert probe.peak > 1, "the worktrees were read one after another"


def test_no_more_reads_run_at_once_than_the_cap_allows(tmp_path):
    """Two unbounded levels multiply, so both share one cap.

    16 projects times 90 worktrees times three subprocesses is enough
    concurrent work to trip ``gh`` secondary rate limits. A rate-limited
    batch returns ``None``, and every row then falls back to the slower
    per-branch lookup — so the failure is silent and backwards.

    ``get_open_prs_async`` is that ``gh`` call, so the probe counts it
    alongside a worktree read: one budget has to cover both levels, or the
    level the cap exists for is the one still running unbounded.
    """
    # More projects than the cap, so the project level alone can exceed it.
    for n in range(8):
        (tmp_path / f"project-{n}" / ".mael").mkdir(parents=True)
    probe = _ConcurrencyProbe()

    async def four_worktrees(project_path):
        return _fake_worktrees(project_path, 4)

    with _quiet_worktree_reads(
        list_worktrees_async=four_worktrees,
        get_open_prs_async=probe.answering({}),
        get_worktree_dirty_files_async=probe.read,
    ):
        data = asyncio.run(build_list_all_data(tmp_path, concurrency=3))

    assert sum(len(p["worktrees"]) for p in data["projects"]) == 32
    assert probe.peak <= 3, f"{probe.peak} reads ran at once, cap was 3"


def test_one_unreadable_worktree_does_not_blank_the_others(tmp_path):
    """A worktree that cannot be read costs its own row, not the project's.

    A worktree directory can go between the listing and the row read, and the
    server polls this every 15s.
    """
    (tmp_path / "alpha" / ".mael").mkdir(parents=True)

    async def three_worktrees(project_path):
        return _fake_worktrees(project_path, 3)

    async def one_worktree_is_gone(worktree_path, *args, **kwargs):
        if worktree_path.name.endswith("-1"):
            raise FileNotFoundError(worktree_path)
        return []

    with _quiet_worktree_reads(
        list_worktrees_async=three_worktrees,
        get_worktree_dirty_files_async=one_worktree_is_gone,
    ):
        data = asyncio.run(build_list_all_data(tmp_path))

    assert [row["name"] for row in data["projects"][0]["worktrees"]] == [
        "alpha-0",
        "alpha-2",
    ]
