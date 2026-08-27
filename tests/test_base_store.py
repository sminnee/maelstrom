"""Tests for maelstrom.base_store backends.

The contract runs against both backends. ``GitConfigBaseStore`` writes to a real
repo's shared config, which is the property the whole design rests on: a linked
worktree resolves ``git config`` to ``$GIT_COMMON_DIR/config``, so every worktree
in a project reads the same bases. (``gh stack`` keeps its state in a plain
``.git/gh-stack`` file instead, which is why it cannot see a linked worktree's
stack at all.)
"""

import subprocess

import pytest

from maelstrom.base_store import GitConfigBaseStore, InMemoryBaseStore
from maelstrom.worktree_model import BaseRef


@pytest.fixture
def git_repo(tmp_path):
    """A repo with one commit, plus a linked worktree that shares its config."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(root)], check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True
    )
    return root


@pytest.fixture(params=["memory", "gitconfig"])
def store(request, git_repo):
    if request.param == "memory":
        return InMemoryBaseStore()
    return GitConfigBaseStore(git_repo)


class TestBaseStoreContract:
    """Shared contract both backends must satisfy."""

    def test_unset_branch_reads_as_the_default_base(self, store):
        assert store.read("feat/a") == BaseRef()

    def test_write_read_round_trip(self, store):
        store.write("feat/a", BaseRef(branch="feat/parent", tip="abc123"))
        assert store.read("feat/a") == BaseRef(branch="feat/parent", tip="abc123")

    def test_a_base_without_a_tip_round_trips(self, store):
        store.write("feat/a", BaseRef(branch="feat/parent"))
        assert store.read("feat/a") == BaseRef(branch="feat/parent", tip=None)

    def test_overwrite_replaces_both_fields(self, store):
        store.write("feat/a", BaseRef(branch="feat/one", tip="aaa"))
        store.write("feat/a", BaseRef(branch="feat/two", tip="bbb"))
        assert store.read("feat/a") == BaseRef(branch="feat/two", tip="bbb")

    def test_overwriting_without_a_tip_clears_a_stale_tip(self, store):
        """A stale tip is worse than none: it would replay from the wrong point."""
        store.write("feat/a", BaseRef(branch="feat/one", tip="aaa"))
        store.write("feat/a", BaseRef(branch="feat/one"))
        assert store.read("feat/a").tip is None

    def test_clear_restores_the_default(self, store):
        store.write("feat/a", BaseRef(branch="feat/parent", tip="abc"))
        store.clear("feat/a")
        assert store.read("feat/a") == BaseRef()

    def test_clear_missing_is_a_noop(self, store):
        store.clear("feat/never-set")

    def test_all_returns_every_stored_base_branch(self, store):
        store.write("feat/b", BaseRef(branch="feat/a", tip="aaa"))
        store.write("feat/c", BaseRef(branch="feat/b"))
        assert store.all() == {"feat/b": "feat/a", "feat/c": "feat/b"}

    def test_all_is_empty_when_nothing_is_stored(self, store):
        assert store.all() == {}

    def test_all_omits_a_cleared_branch(self, store):
        store.write("feat/b", BaseRef(branch="feat/a"))
        store.clear("feat/b")
        assert store.all() == {}

    def test_branch_names_with_slashes_and_dashes_round_trip(self, store):
        """Real branch names are ``feat/some-thing``; the key must survive them."""
        store.write("feat/some-thing", BaseRef(branch="fix/other-thing", tip="c0ffee"))
        assert store.read("feat/some-thing").branch == "fix/other-thing"
        assert store.all() == {"feat/some-thing": "fix/other-thing"}

    def test_stack_tip_defaults_to_main(self, store):
        assert store.read_stack_tip() == "main"

    def test_stack_tip_round_trips(self, store):
        store.write_stack_tip("feat/a")
        assert store.read_stack_tip() == "feat/a"

    def test_stack_tip_resets_to_main(self, store):
        store.write_stack_tip("feat/a")
        store.write_stack_tip("main")
        assert store.read_stack_tip() == "main"


class TestGitConfigBaseStore:
    """Behaviour specific to the git-config backend."""

    def test_a_linked_worktree_reads_the_same_bases(self, git_repo, tmp_path):
        """The property ``gh stack``'s state file lacks, and the reason for this design."""
        store = GitConfigBaseStore(git_repo)
        store.write("feat/child", BaseRef(branch="feat/parent", tip="abc123"))

        linked = tmp_path / "linked"
        subprocess.run(
            ["git", "worktree", "add", "-b", "feat/child", str(linked), "main"],
            cwd=git_repo,
            check=True,
            capture_output=True,
        )
        assert GitConfigBaseStore(linked).read("feat/child") == BaseRef(
            branch="feat/parent", tip="abc123"
        )

    def test_a_mixed_case_branch_name_round_trips(self, git_repo):
        """git lowercases the third config component on read; the store normalises.

        ``branch.<name>.maelBaseTip`` comes back as ``maelbasetip``, so a naive
        parse of ``--get-regexp`` output would drop or mis-key the entry.
        """
        store = GitConfigBaseStore(git_repo)
        store.write("feat/MixedCase", BaseRef(branch="feat/Parent", tip="abc"))
        assert store.read("feat/MixedCase") == BaseRef(branch="feat/Parent", tip="abc")
        assert store.all() == {"feat/MixedCase": "feat/Parent"}

    def test_all_uses_one_subprocess(self, git_repo, monkeypatch):
        """Batch callers must never pay a subprocess per worktree."""
        import maelstrom.base_store as mod

        calls = []
        real = mod.run_cmd

        def counting(cmd, **kwargs):
            calls.append(cmd)
            return real(cmd, **kwargs)

        store = GitConfigBaseStore(git_repo)
        store.write("feat/b", BaseRef(branch="feat/a"))
        store.write("feat/c", BaseRef(branch="feat/b"))
        monkeypatch.setattr(mod, "run_cmd", counting)
        store.all()
        assert len(calls) == 1

    def test_a_failed_write_raises_rather_than_reporting_success(self, tmp_path):
        """A silently dropped write is the one failure this store must not have.

        The tip would keep a stale value, and the next rebase would replay from
        the wrong point with nothing reporting why — the exact case the tip exists
        to prevent.
        """
        store = GitConfigBaseStore(tmp_path / "does-not-exist")
        with pytest.raises(RuntimeError, match="Could not write git config"):
            store.write("feat/child", BaseRef(branch="feat/parent"))

    def test_clearing_an_unset_branch_is_still_a_noop(self, git_repo):
        """``git config --unset`` exits 5 for a missing key; that is success here."""
        GitConfigBaseStore(git_repo).clear("feat/never-set")

    def test_reads_still_degrade_quietly(self, tmp_path):
        """Reads decorate output, so an unreadable store must not fail the command."""
        store = GitConfigBaseStore(tmp_path / "does-not-exist")
        assert store.read("feat/child") == BaseRef()
        assert store.all() == {}
        assert store.read_stack_tip() == "main"

    def test_writes_land_in_shared_config_not_worktree_config(self, git_repo):
        """``--worktree`` config would not be shared; assert the plain form is used."""
        GitConfigBaseStore(git_repo).write("feat/child", BaseRef(branch="feat/parent"))
        config = (git_repo / ".git" / "config").read_text()
        assert "feat/parent" in config
