"""The Build worktree pool's git helpers, on real git."""

from git_helpers import (
    create_commit,
    is_detached,
    push_to_origin_main,
    rev_parse,
    run_git,
)

from mael_domain.worktree import (
    ensure_build_worktree,
    is_based_on_origin_main,
    prepare_build,
)


def test_a_branch_from_the_main_tip_is_based_and_names_the_base(project_with_worktree):
    project, worktree, _ = project_with_worktree
    create_commit(worktree, "a.txt", "a\n", "feat: a")
    assert is_based_on_origin_main(worktree) == rev_parse(project, "origin/main")


def test_a_branch_behind_main_is_not_based(project_with_worktree):
    project, worktree, remote = project_with_worktree
    create_commit(worktree, "a.txt", "a\n", "feat: a")
    push_to_origin_main(project, remote)
    assert is_based_on_origin_main(worktree) is None


def test_a_build_worktree_is_made_once_detached_with_an_env(project_with_worktree):
    project, _, _ = project_with_worktree
    path = ensure_build_worktree(project, "_build1")
    assert path == project / "_build1"
    assert is_detached(path)
    assert "WORKTREE=_build1" in (path / ".env").read_text()
    (path / "marker").write_text("kept")
    assert ensure_build_worktree(project, "_build1") == path
    assert (path / "marker").exists()


def test_prepare_build_cleans_a_dirty_worktree_and_keeps_the_env(
    project_with_worktree,
):
    project, worktree, _ = project_with_worktree
    # A project ignores `.env`, so `git clean -fd` keeps it; `-fdx` would not.
    sha = create_commit(worktree, ".gitignore", ".env\n", "chore: ignore env")
    run_git(worktree, "push", "origin", "feature/work")
    path = ensure_build_worktree(project, "_build1")
    (path / "README.md").write_text("edited by a failed gate\n")
    (path / "litter.txt").write_text("untracked\n")

    prepare_build(path, sha)

    assert rev_parse(path) == sha
    assert run_git(path, "status", "--porcelain").stdout == ""
    assert (path / ".env").exists()
