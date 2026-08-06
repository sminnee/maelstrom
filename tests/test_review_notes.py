"""Tests for the git-note mechanics that /code-review relies on.

``/code-review`` marks a reviewed commit with a ``reviewed`` note on
``refs/notes/commits``, and skips any commit that carries one on a later run.
The skill is prompt-driven, so there is no maelstrom code to test — what these
tests pin down is the git behaviour the design rests on, so a change in git or
in project config cannot break the skip silently.

See ``shared/skills/code-review/SKILL.md`` steps 3b and 7b.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from tests.git_helpers import create_commit, run_git, setup_git_repo, setup_origin_main

REVIEWED = "reviewed"


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


def tag_reviewed(repo, sha):
    """Mark a commit reviewed, exactly as SKILL.md step 7b does."""
    run_git(repo, "notes", "add", "-f", "-m", REVIEWED, sha)


def is_reviewed(repo, sha):
    """Whether a commit would be skipped by SKILL.md step 3b."""
    result = run_git(repo, "notes", "show", sha, check=False)
    if result.returncode != 0:
        return False
    return REVIEWED in result.stdout.splitlines()


def head(repo):
    return run_git(repo, "rev-parse", "HEAD").stdout.strip()


def squash(repo):
    """Autosquash fixups onto origin/main, as 'mael git squash' does."""
    run_git(
        repo,
        "-c",
        "sequence.editor=true",
        "rebase",
        "--autostash",
        "--autosquash",
        "origin/main",
    )


def advance_origin_main(repo):
    """Put a commit on origin/main that the branch does not have.

    A rebase only replays the branch when the base has moved, so this is what
    makes the following tests rewrite SHAs the way a real 'mael sync' does.
    """
    branch = run_git(repo, "rev-parse", "HEAD").stdout.strip()
    run_git(repo, "checkout", "--detach", "refs/remotes/origin/main")
    create_commit(repo, "upstream.txt", "upstream", "Upstream commit")
    run_git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    run_git(repo, "checkout", branch)


def test_note_survives_rebase_so_commit_is_skipped(repo):
    """A reviewed commit stays reviewed after a rebase that rewrites its SHA."""
    create_commit(repo, "work.txt", "work", "Work commit")
    old_sha = head(repo)
    tag_reviewed(repo, old_sha)

    advance_origin_main(repo)
    squash(repo)

    assert head(repo) != old_sha, "expected the rebase to rewrite the SHA"
    assert is_reviewed(repo, head(repo))


def test_note_is_dropped_without_rewrite_ref_config(repo):
    """Without notes.rewriteRef the note is lost, so nothing is ever skipped.

    This is the silent-degradation mode that mael doctor's
    _check_notes_rewrite_ref exists to prevent.
    """
    run_git(repo, "config", "--unset-all", "notes.rewriteRef")
    create_commit(repo, "work.txt", "work", "Work commit")
    old_sha = head(repo)
    tag_reviewed(repo, old_sha)

    advance_origin_main(repo)
    squash(repo)

    assert head(repo) != old_sha, "expected the rebase to rewrite the SHA"
    assert not is_reviewed(repo, head(repo))


def test_tagged_fixup_marks_the_squashed_commit_reviewed(repo):
    """Termination: tagging the fixup stops the fixed commit coming back.

    A commit with findings is left untagged and its fixup is tagged instead.
    After the squash the combined commit reads 'reviewed', so review-and-fix
    cannot loop. If this regresses, reviews can run indefinitely.
    """
    create_commit(repo, "work.txt", "work", "Work commit")
    target = head(repo)

    (repo / "work.txt").write_text("fixed")
    run_git(repo, "add", "work.txt")
    run_git(repo, "commit", f"--fixup={target}")
    tag_reviewed(repo, head(repo))

    squash(repo)

    assert is_reviewed(repo, head(repo))
    assert (repo / "work.txt").read_text() == "fixed"


def test_commit_with_findings_and_no_fixup_stays_unreviewed(repo):
    """A commit that had findings is re-reviewed when its fixup is never made.

    Tags a sibling in the same rebase, so this pins that the note is per-commit
    rather than merely that an untagged commit reads as untagged.
    """
    create_commit(repo, "clean.txt", "clean", "Clean commit")
    clean = head(repo)
    tag_reviewed(repo, clean)
    create_commit(repo, "work.txt", "work", "Commit with findings")

    advance_origin_main(repo)
    squash(repo)

    reviewed_head, reviewed_parent = (
        is_reviewed(repo, head(repo)),
        is_reviewed(repo, run_git(repo, "rev-parse", "HEAD~1").stdout.strip()),
    )
    assert not reviewed_head, "the commit with findings must come back for review"
    assert reviewed_parent, "its clean neighbour must stay skipped"


def test_second_add_overwrites_rather_than_concatenating(repo):
    """'notes add -f' replaces the note; without -f git would stack them."""
    create_commit(repo, "work.txt", "work", "Work commit")
    sha = head(repo)

    tag_reviewed(repo, sha)
    tag_reviewed(repo, sha)

    note = run_git(repo, "notes", "show", sha).stdout.strip()
    assert note == REVIEWED
    listed = run_git(repo, "notes", "list", sha).stdout.split()
    assert len(listed) == 1


def test_remove_clears_a_prior_tag(repo):
    """A stale tag can be cleared when the commit is later found wanting."""
    create_commit(repo, "work.txt", "work", "Work commit")
    sha = head(repo)
    tag_reviewed(repo, sha)

    run_git(repo, "notes", "remove", "--ignore-missing", sha)

    assert not is_reviewed(repo, sha)
    # Removing a note that is already gone is not an error.
    run_git(repo, "notes", "remove", "--ignore-missing", sha)


def test_accepted_gap_modified_commit_keeps_its_note(repo):
    """ACCEPTED GAP, pinned so a future change to it is deliberate.

    A commit reviews clean and is tagged. A later fixup modifies it. After the
    squash the note still reads 'reviewed', so the changed code is skipped and
    never reviewed. This is the same note-outlives-content behaviour that makes
    the fixup tag work; the two cannot be separated without recording what the
    note was written against. SKILL.md step 3b reports every skip so the gap is
    at least visible.
    """
    create_commit(repo, "work.txt", "work", "Work commit")
    target = head(repo)
    tag_reviewed(repo, target)

    (repo / "work.txt").write_text("changed after review")
    run_git(repo, "add", "work.txt")
    run_git(repo, "commit", f"--fixup={target}")

    squash(repo)

    assert is_reviewed(repo, head(repo))
    assert (repo / "work.txt").read_text() == "changed after review"
