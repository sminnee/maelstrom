"""``build_list_all_data`` on a real project layout.

The rows feed both ``mael list-all`` and the orchestrator server, so they are
checked here once, against the bare-clone-plus-worktree fixture.
"""

from pathlib import Path
from unittest.mock import patch

from maelstrom.list_all import build_list_all_data
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
