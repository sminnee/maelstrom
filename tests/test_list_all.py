"""``build_list_all_data`` on a real project layout.

The rows feed both ``mael list-all`` and the orchestrator server, so they are
checked here once, against the bare-clone-plus-worktree fixture.
"""

import dataclasses
from pathlib import Path
from unittest.mock import patch

import pytest

from maelstrom.github_model import PrStatus
from maelstrom.list_all import (
    build_list_all_data,
    project_repo_url,
    repo_url_from_remote,
)
from maelstrom.worktree import list_worktrees, run_git
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
        patch("maelstrom.list_all.get_open_prs", return_value={}),
        patch("maelstrom.session_discovery.LiveSessionSet.count_for", return_value=0),
    ):
        data = build_list_all_data(project_path.parent)

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
        patch("maelstrom.list_all.get_open_prs", return_value={}),
        patch("maelstrom.session_discovery.LiveSessionSet.count_for", return_value=0),
    ):
        data = build_list_all_data(project_path.parent)

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
        patch("maelstrom.list_all.get_open_prs", return_value=batch),
        patch("maelstrom.session_discovery.LiveSessionSet.count_for", return_value=0),
    ):
        data = build_list_all_data(project_path.parent)
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
    with patch("maelstrom.list_all.get_pushed_commit_count", return_value=4):
        row = _row_for(project_path, _pr(42, state="merged"))
    assert row["pushed_commits"] == 4


def test_an_open_pr_replaces_the_pushed_commit_count(
    project_with_worktree,  # noqa: F811
):
    project_path, _worktree_path, _remote = project_with_worktree
    (project_path / ".mael").touch()
    with patch("maelstrom.list_all.get_pushed_commit_count", return_value=4):
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
        patch("maelstrom.list_all.get_open_prs", return_value={}),
        patch("maelstrom.session_discovery.LiveSessionSet.count_for", return_value=0),
    ):
        data = build_list_all_data(project_path.parent)

    assert data["projects"][0]["worktrees"][0]["pr_url"] is None


def test_a_project_dir_that_is_not_a_git_repo_carries_no_repo_url(tmp_path):
    """`list-all` visits every project dir. A non-repo must not break the read."""
    project_path = tmp_path / "test-repo"
    project_path.mkdir()
    (project_path / ".mael").touch()
    with (
        patch("maelstrom.list_all.get_open_prs", return_value={}),
        patch("maelstrom.session_discovery.LiveSessionSet.count_for", return_value=0),
    ):
        data = build_list_all_data(tmp_path)

    assert data["projects"][0]["repo_url"] is None


def test_a_project_path_that_does_not_exist_carries_no_repo_url(tmp_path):
    """A project dir can vanish between the scan and the read. That is not a crash."""
    missing = tmp_path / "gone"
    assert project_repo_url(missing) is None


def test_a_project_with_no_remote_carries_no_repo_url(tmp_path, monkeypatch):
    """A project with no origin must still list; the card then draws no PR link."""
    project_path = tmp_path / "test-repo"
    project_path.mkdir()
    (project_path / ".mael").touch()
    run_git(["init", "-q", str(project_path)])
    with (
        patch("maelstrom.list_all.get_open_prs", return_value={}),
        patch("maelstrom.session_discovery.LiveSessionSet.count_for", return_value=0),
    ):
        data = build_list_all_data(tmp_path)

    assert data["projects"][0]["repo_url"] is None


def test_a_projects_dir_with_no_projects_is_empty(tmp_path):
    assert build_list_all_data(tmp_path) == {"projects": []}


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
        patch("maelstrom.list_all.get_open_prs", return_value={}),
        patch("maelstrom.session_discovery.LiveSessionSet.count_for", return_value=0),
    ):
        data = build_list_all_data(link)
    assert [row["name"] for row in data["projects"][0]["worktrees"]] == ["alpha"]
