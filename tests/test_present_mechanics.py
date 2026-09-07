"""Pins the interaction between /present and /code-review's reviewed notes.

``mael doctor`` sets ``notes.rewriteRef`` so a ``reviewed`` note follows its
commit through a rebase, and ``/code-review`` skips any commit carrying one.
``/present`` makes new commit objects, so the notes do not follow — which is
what makes review read a presented branch in full.

The failure this guards is silent: were the notes ever to propagate, review
would skip every story commit and report the branch as fully reviewed.

``tests/test_uncommit_branch.py`` owns the reset and the history ref, against
the real function. See ``shared/skills/present/SKILL.md``.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from tests.git_helpers import create_commit, run_git, setup_git_repo, setup_origin_main


@pytest.fixture
def repo():
    """A repo with one commit on main and origin/main pointing at it."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp)
        setup_git_repo(path)
        run_git(path, "config", "notes.rewriteRef", "refs/notes/*")
        create_commit(path, "base.txt", "base", "Base commit")
        setup_origin_main(path)
        yield path


def head(repo):
    return run_git(repo, "rev-parse", "HEAD").stdout.strip()


def uncommit(repo):
    """Reset to the fork point with every change unstaged.

    Mirrors the state ``uncommit_branch`` leaves behind — it resets ``--mixed``
    onto the fork point. ``tests/test_uncommit_branch.py`` owns the command
    itself, including the history ref and its survival of a prune.
    """
    run_git(repo, "reset", "--mixed", "refs/remotes/origin/main")


def test_story_commit_carries_no_reviewed_note(repo):
    """A presented branch is reviewed fresh, even with notes.rewriteRef set.

    ``notes.rewriteRef`` copies a note when git *rewrites* a commit. Reset plus
    commit is not a rewrite, so the note stays on the working history and the
    story commit has none. /code-review therefore reviews every story commit.
    """
    create_commit(repo, "one.txt", "one", "First step")
    reviewed = head(repo)
    run_git(repo, "notes", "add", "-f", "-m", "reviewed", reviewed)

    uncommit(repo)
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-m", "feat: the one decision")

    note = run_git(repo, "notes", "show", head(repo), check=False)
    assert note.returncode != 0, f"story commit unexpectedly reviewed: {note.stdout}"
