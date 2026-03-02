"""E2E workflow tests for worktree lifecycle and port allocation.

Consolidates worktree create/list/sync/close/recycle/remove and port
allocation tests into workflow tests sharing a single git project.
"""

import subprocess

import pytest

from maelstrom.ports import get_port_allocation, load_port_allocations
from maelstrom.worktree import (
    close_worktree,
    create_worktree,
    get_worktree_folder_name,
    list_worktrees,
    recycle_worktree,
    remove_worktree_by_path,
    sync_worktree,
    read_env_file,
)

from .conftest import create_commit, run_git


@pytest.mark.e2e
class TestWorktreeFullLifecycle:
    """Full worktree lifecycle: create → ports → list → sync → close → recycle → remove."""

    def test_worktree_full_lifecycle(self, git_project_module):
        """Exercise the complete worktree lifecycle in a single test."""
        gp = git_project_module

        # --- Phase 1: Create worktree, verify basics ---
        wt_path = create_worktree(gp.project_path, "feature/lifecycle-test")
        assert wt_path.exists()
        assert wt_path.is_dir()
        assert "bravo" in wt_path.name

        result = run_git(wt_path, "branch", "--show-current")
        assert "feature/lifecycle-test" in result.stdout.strip()

        env_vars = read_env_file(wt_path)
        assert "PORT_BASE" in env_vars
        assert "WORKTREE" in env_vars
        assert env_vars["WORKTREE"] == "bravo"

        alloc = get_port_allocation(gp.project_path, "bravo")
        assert alloc is not None
        assert alloc == int(env_vars["PORT_BASE"])
        bravo_port_base = alloc

        # --- Phase 2: Create more worktrees, verify unique ports ---
        port_bases = {bravo_port_base}
        extra_paths = []
        for i in range(2):
            p = create_worktree(gp.project_path, f"feature/port-test-{i}")
            extra_paths.append(p)
            ev = read_env_file(p)
            pb = int(ev["PORT_BASE"])
            assert pb not in port_bases, f"Duplicate PORT_BASE {pb}"
            port_bases.add(pb)

        allocations = load_port_allocations()
        project_key = str(gp.project_path.resolve())
        assert project_key in allocations
        assert len(allocations[project_key]) >= 3

        # --- Phase 3: List worktrees ---
        worktrees = list_worktrees(gp.project_path)
        names = [wt.path.name for wt in worktrees]
        assert any("alpha" in n for n in names)
        assert any("bravo" in n for n in names)

        # --- Phase 4: .env template substitution ---
        project_env = gp.project_path / ".env"
        project_env.write_text("APP_URL=http://localhost:${FRONTEND_PORT}\n")
        subst_path = create_worktree(gp.project_path, "feature/subst-test")
        subst_env = read_env_file(subst_path)
        assert "APP_URL" in subst_env
        assert "FRONTEND_PORT" in subst_env
        assert int(subst_env["FRONTEND_PORT"]) > 0
        raw_env = (subst_path / ".env").read_text()
        assert "APP_URL" in raw_env
        assert "FRONTEND_PORT=" in raw_env
        # Clean up .env template
        project_env.unlink()

        # --- Phase 5: Sync clean ---
        create_commit(wt_path, "feature.txt", "feature content", "Feature commit")

        source_clone = gp.projects_dir.parent / "sync-source"
        subprocess.run(
            ["git", "clone", str(gp.remote_path), str(source_clone)],
            check=True, capture_output=True,
        )
        run_git(source_clone, "config", "user.email", "test@test.com")
        run_git(source_clone, "config", "user.name", "Test")
        create_commit(source_clone, "main-update.txt", "main update", "Main update")
        run_git(source_clone, "push", "origin", "main")

        sync_result = sync_worktree(wt_path)
        assert sync_result.success, f"Sync failed: {sync_result.message}"
        assert not sync_result.had_conflicts

        # --- Phase 6: Close dirty → fails ---
        (wt_path / "dirty.txt").write_text("uncommitted")
        run_git(wt_path, "add", "dirty.txt")

        close_result = close_worktree(wt_path)
        assert not close_result.success
        assert close_result.had_dirty_files

        # Undo dirty state
        run_git(wt_path, "reset", "HEAD", "dirty.txt")
        (wt_path / "dirty.txt").unlink()

        # --- Phase 7: Close unpushed → fails ---
        create_commit(wt_path, "unpushed.txt", "content", "Unpushed commit")

        close_result = close_worktree(wt_path)
        assert not close_result.success
        assert close_result.had_unpushed_commits

        # --- Phase 8: Push + merge → close succeeds, port freed ---
        run_git(wt_path, "push", "origin", "feature/lifecycle-test")

        head_result = run_git(wt_path, "rev-parse", "HEAD")
        commit_sha = head_result.stdout.strip()
        run_git(gp.remote_path, "update-ref", "refs/heads/main", commit_sha)
        run_git(gp.project_path, "fetch", "origin")

        close_result = close_worktree(wt_path)
        assert close_result.success, f"Close failed: {close_result.message}"

        alloc = get_port_allocation(gp.project_path, "bravo")
        assert alloc is None

        # --- Phase 9: Recycle to new branch ---
        recycled_path = recycle_worktree(wt_path, "feature/recycled")
        assert recycled_path == wt_path

        result = run_git(wt_path, "branch", "--show-current")
        assert "feature/recycled" in result.stdout.strip()

        new_env = read_env_file(wt_path)
        assert new_env.get("PORT_BASE") is not None

        # Push + merge to allow closing
        create_commit(wt_path, "recycled.txt", "recycled", "Recycled commit")
        run_git(wt_path, "push", "origin", "feature/recycled")
        head_result = run_git(wt_path, "rev-parse", "HEAD")
        run_git(gp.remote_path, "update-ref", "refs/heads/main", head_result.stdout.strip())
        run_git(gp.project_path, "fetch", "origin")
        close_worktree(wt_path)

        # --- Phase 10: Remove a worktree ---
        remove_path = extra_paths[0]
        assert remove_path.exists()

        # Figure out the NATO name from the path
        nato_name = remove_path.name.split("-")[-1]
        alloc_before = get_port_allocation(gp.project_path, nato_name)
        assert alloc_before is not None

        folder_name = get_worktree_folder_name(gp.project_name, nato_name)
        remove_worktree_by_path(gp.project_path, folder_name)
        assert not remove_path.exists()

        alloc_after = get_port_allocation(gp.project_path, nato_name)
        assert alloc_after is None

        # --- Phase 11: Freed port is reusable ---
        reuse_path = create_worktree(gp.project_path, "feature/port-reuse-test")
        reuse_env = read_env_file(reuse_path)
        new_base = int(reuse_env["PORT_BASE"])
        assert 300 <= new_base <= 999


@pytest.mark.e2e
class TestWorktreeSyncConflicts:
    """Sync with conflicts (separate test since conflict state is messy)."""

    def test_sync_with_conflicts(self, git_project_module):
        """Sync detects and reports conflicts."""
        gp = git_project_module

        branch = "feature/conflict-test"
        wt_path = create_worktree(gp.project_path, branch)

        # Modify the same file on the feature branch
        create_commit(wt_path, "README.md", "feature version", "Feature change")

        # Modify the same file on origin/main
        source_clone = gp.projects_dir.parent / "conflict-source"
        subprocess.run(
            ["git", "clone", str(gp.remote_path), str(source_clone)],
            check=True, capture_output=True,
        )
        run_git(source_clone, "config", "user.email", "test@test.com")
        run_git(source_clone, "config", "user.name", "Test")
        create_commit(source_clone, "README.md", "main version", "Main change")
        run_git(source_clone, "push", "origin", "main")

        sync_result = sync_worktree(wt_path)
        assert not sync_result.success
        assert sync_result.had_conflicts

        # Clean up rebase state
        run_git(wt_path, "rebase", "--abort", check=False)
