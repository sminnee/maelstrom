"""Tests for the pure project-rename model."""

from pathlib import Path

import pytest

from maelstrom.mv_project import (
    build_move_plan,
    new_worktree_folder,
    rekey_claude_json,
    rekey_port_allocations,
    repoint_path,
    worktree_nato_name,
)
from maelstrom.task import session_id_for


class TestNewWorktreeFolder:
    """Folder-name mapping across a project rename."""

    def test_maps_a_nato_folder_to_the_new_project(self):
        assert new_worktree_folder("old", "new", "old-alpha") == "new-alpha"

    def test_maps_every_nato_name(self):
        for nato in ("alpha", "bravo", "zulu"):
            assert new_worktree_folder("old", "new", f"old-{nato}") == f"new-{nato}"

    def test_leaves_main_unchanged(self):
        """`_main` is a real worktree that does not follow the convention."""
        assert new_worktree_folder("old", "new", "_main") == "_main"

    def test_leaves_a_nato_lookalike_unchanged(self):
        """`old-alphabet` is not a worktree, so it must not be rewritten."""
        assert new_worktree_folder("old", "new", "old-alphabet") == "old-alphabet"

    def test_leaves_an_unrelated_folder_unchanged(self):
        assert new_worktree_folder("old", "new", "other-alpha") == "other-alpha"

    def test_nato_name_extraction(self):
        assert worktree_nato_name("old", "old-alpha") == "alpha"
        assert worktree_nato_name("old", "_main") is None
        assert worktree_nato_name("old", "old-alphabet") is None


class TestBuildMovePlan:
    """Plan construction from a set of facts."""

    def _plan(self, **overrides):
        kwargs: dict = {
            "old_name": "old",
            "new_name": "new",
            "projects_dir": Path("/Projects"),
            "worktree_folders": ["old-alpha", "old-bravo", "_main"],
            "task_ids": ["t1", "t2"],
            "ran_task_ids": set(),
            "home": Path("/home/u"),
        }
        kwargs.update(overrides)
        return build_move_plan(**kwargs)

    def test_project_dir_moves_last(self):
        """Worktrees move inside the old dir first, so no path goes stale."""
        plan = self._plan()
        last = plan.dir_moves[-1]

        assert last.src == Path("/Projects/old")
        assert last.dst == Path("/Projects/new")
        assert last.is_worktree is False
        assert all(m.is_worktree for m in plan.dir_moves[:-1])

    def test_worktree_moves_stay_inside_the_old_project_dir(self):
        plan = self._plan()
        alpha = next(m for m in plan.worktree_moves if m.nato == "alpha")

        assert alpha.src == Path("/Projects/old/old-alpha")
        assert alpha.dst == Path("/Projects/old/new-alpha")

    def test_main_worktree_is_included_but_not_renamed(self):
        plan = self._plan()
        main = next(m for m in plan.worktree_moves if m.src.name == "_main")

        assert main.dst.name == "_main"
        assert main.renamed is False
        assert main.nato is None

    def test_port_keys_are_the_absolute_project_paths(self):
        plan = self._plan()

        assert plan.port_key_old == "/Projects/old"
        assert plan.port_key_new == "/Projects/new"

    def test_claude_dirs_cover_the_project_and_every_worktree(self):
        plan = self._plan()

        assert len(plan.claude_dir_moves) == 4  # project + 3 worktrees
        old_dirs = [str(old) for old, _ in plan.claude_dir_moves]
        assert "/home/u/.claude/projects/-Projects-old" in old_dirs

    def test_claude_slugs_are_derived_from_the_final_paths(self):
        """Slugs are lossy, so they must be computed forwards from new paths."""
        plan = self._plan()
        new_dirs = [str(new) for _, new in plan.claude_dir_moves]

        assert "/home/u/.claude/projects/-Projects-new" in new_dirs
        assert "/home/u/.claude/projects/-Projects-new-new-alpha" in new_dirs
        # `_main` keeps its folder name but still lands under the new project.
        assert "/home/u/.claude/projects/-Projects-new-_main" in new_dirs

    def test_task_rekeys_use_each_task_status(self):
        plan = self._plan(
            task_ids=["t1", "t2"],
            task_statuses={"t1": "in-progress", "t2": "done"},
        )

        assert ("old/in-progress/t1.md", "new/in-progress/t1.md") in plan.task_rekeys
        assert ("old/done/t2.md", "new/done/t2.md") in plan.task_rekeys

    def test_orphaned_sessions_are_counted_and_warned_about(self):
        plan = self._plan(ran_task_ids={"t1"})

        assert plan.orphaned_session_count == 1
        assert any("orphaned" in w for w in plan.warnings)

    def test_no_warning_when_nothing_ever_ran(self):
        plan = self._plan(ran_task_ids=set())

        assert plan.orphaned_session_count == 0
        assert plan.warnings == []

    def test_ran_ids_outside_the_project_are_ignored(self):
        plan = self._plan(task_ids=["t1"], ran_task_ids={"t1", "stranger"})

        assert plan.orphaned_session_count == 1

    def test_only_symlinks_pointing_into_the_project_are_repointed(self):
        plan = self._plan(
            global_symlinks=[
                (
                    Path("/home/u/.claude/skills/mael"),
                    Path("/Projects/old/_main/shared/skills/mael"),
                ),
                (Path("/home/u/.claude/skills/other"), Path("/elsewhere/skills/other")),
            ],
        )

        assert plan.symlink_repoints == [
            (
                Path("/home/u/.claude/skills/mael"),
                Path("/Projects/new/_main/shared/skills/mael"),
            ),
        ]

    def test_a_symlink_through_a_renamed_worktree_folder_follows_it(self):
        """The worktree folder is renamed too, so the target must map both."""
        plan = self._plan(
            global_symlinks=[
                (
                    Path("/home/u/.claude/skills/mael"),
                    Path("/Projects/old/old-alpha/shared/skills/mael"),
                ),
            ],
        )

        assert plan.symlink_repoints == [
            (
                Path("/home/u/.claude/skills/mael"),
                Path("/Projects/new/new-alpha/shared/skills/mael"),
            ),
        ]

    def test_only_claude_json_keys_under_the_project_are_rekeyed(self):
        plan = self._plan(
            claude_json_projects=["/Projects/old/_main", "/Projects/unrelated"],
        )

        assert plan.claude_json_rekeys == [
            ("/Projects/old/_main", "/Projects/new/_main"),
        ]

    def test_a_claude_json_key_under_a_renamed_worktree_follows_it(self):
        """`new/old-alpha` would be a path that never exists."""
        plan = self._plan(claude_json_projects=["/Projects/old/old-alpha"])

        assert plan.claude_json_rekeys == [
            ("/Projects/old/old-alpha", "/Projects/new/new-alpha"),
        ]

    def test_rejects_an_unchanged_name(self):
        with pytest.raises(ValueError, match="same"):
            self._plan(new_name="old")

    def test_rejects_an_empty_name(self):
        with pytest.raises(ValueError, match="empty"):
            self._plan(new_name="")

    def test_rejects_a_name_with_a_path_separator(self):
        with pytest.raises(ValueError, match="Invalid project name"):
            self._plan(new_name="a/b")


class TestRekeyPortAllocations:
    """Re-keying the allocations file, which is keyed by absolute path."""

    def test_moves_the_project_entry(self):
        result = rekey_port_allocations({"/p/old": {"alpha": 310}}, "/p/old", "/p/new")

        assert result == {"/p/new": {"alpha": 310}}

    def test_preserves_unrelated_projects(self):
        result = rekey_port_allocations(
            {"/p/old": {"alpha": 310}, "/p/other": {"alpha": 320}},
            "/p/old",
            "/p/new",
        )

        assert result["/p/other"] == {"alpha": 320}

    def test_does_not_mutate_the_input(self):
        original = {"/p/old": {"alpha": 310}}
        rekey_port_allocations(original, "/p/old", "/p/new")

        assert original == {"/p/old": {"alpha": 310}}

    def test_raises_on_collision(self):
        with pytest.raises(ValueError, match="already exist"):
            rekey_port_allocations(
                {"/p/old": {"alpha": 310}, "/p/new": {"alpha": 320}},
                "/p/old",
                "/p/new",
            )

    def test_a_missing_project_is_a_no_op(self):
        result = rekey_port_allocations({"/p/other": {}}, "/p/old", "/p/new")

        assert result == {"/p/other": {}}


class TestRekeyClaudeJson:
    """Re-keying trust and permission entries in ~/.claude.json."""

    def test_rekeys_the_projects_dict(self):
        data = {"projects": {"/p/old": {"trust": True}}}
        result = rekey_claude_json(data, [("/p/old", "/p/new")])

        assert result["projects"] == {"/p/new": {"trust": True}}

    def test_preserves_unrelated_entries(self):
        data = {"projects": {"/p/old": {}, "/p/keep": {"trust": True}}}
        result = rekey_claude_json(data, [("/p/old", "/p/new")])

        assert result["projects"]["/p/keep"] == {"trust": True}

    def test_rekeys_github_repo_paths(self):
        data = {"githubRepoPaths": ["/p/old", "/p/other"]}
        result = rekey_claude_json(data, [("/p/old", "/p/new")])

        assert result["githubRepoPaths"] == ["/p/new", "/p/other"]

    def test_does_not_mutate_the_input(self):
        data = {"projects": {"/p/old": {}}}
        rekey_claude_json(data, [("/p/old", "/p/new")])

        assert data == {"projects": {"/p/old": {}}}

    def test_leaves_other_top_level_keys_alone(self):
        data = {"projects": {"/p/old": {}}, "numStartups": 7}
        result = rekey_claude_json(data, [("/p/old", "/p/new")])

        assert result["numStartups"] == 7

    def test_tolerates_a_file_with_no_projects_key(self):
        assert rekey_claude_json({"numStartups": 1}, [("/a", "/b")]) == {
            "numStartups": 1
        }


class TestRepointPath:
    """The shared under-the-old-root test-and-transform."""

    def test_rewrites_a_path_under_the_root(self):
        assert repoint_path(Path("/p/old/sub"), Path("/p/old"), Path("/p/new")) == Path(
            "/p/new/sub"
        )

    def test_returns_none_for_an_unrelated_path(self):
        assert repoint_path(Path("/other"), Path("/p/old"), Path("/p/new")) is None


class TestSessionIdOrphaning:
    """Regression guard for the accepted, warned-about session orphaning."""

    def test_renaming_a_project_changes_every_session_id(self):
        """Session ids are uuid5 over the project name, so a rename orphans them.

        This is by design (the plan warns rather than migrating transcripts). If
        this test ever fails, session ids became name-independent and the
        warning in the rename plan is wrong.
        """
        for task_id in ("t1", "2026-01-01.1", "deep.nested.id"):
            assert session_id_for("old", task_id) != session_id_for("new", task_id)

    def test_the_same_project_and_task_stay_stable(self):
        assert session_id_for("proj", "t1") == session_id_for("proj", "t1")
