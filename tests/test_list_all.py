"""``build_list_all_data`` on a real project layout.

The rows feed both ``mael list-all`` and the orchestrator server, so they are
checked here once, against the bare-clone-plus-worktree fixture.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from maelstrom.list_all import (
    build_list_all_data,
    project_repo_url,
    repo_url_from_remote,
)
from maelstrom.worktree import run_git
from tests.test_sync_flags import project_with_worktree  # noqa: F401  (fixture)


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


def test_a_worktree_row_carries_its_pr_url(
    project_with_worktree,  # noqa: F811
):
    """The row carries the PR URL, so no reader joins a project field to a row."""
    project_path, _worktree_path, _remote = project_with_worktree
    (project_path / ".mael").touch()
    run_git(
        ["config", "remote.origin.url", "git@github.com:test/test-repo.git"],
        cwd=project_path,
    )
    with (
        patch("maelstrom.list_all.get_open_prs", return_value={}),
        patch("maelstrom.list_all.resolve_pr", return_value=(42, 3)),
        patch("maelstrom.session_discovery.LiveSessionSet.count_for", return_value=0),
    ):
        data = build_list_all_data(project_path.parent)

    row = data["projects"][0]["worktrees"][0]
    assert row["pr_url"] == "https://github.com/test/test-repo/pull/42"


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
