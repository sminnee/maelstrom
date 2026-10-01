"""A worktree's changes, read from real repositories.

Every case builds a repo with :mod:`git_helpers`, because the reads are a parse
of git's own output: a faked ``git diff`` would test the fake.
"""

import pytest
from agent_fixtures import make_change_comment
from git_helpers import create_commit, run_git, setup_git_repo, setup_origin_main

from mael_domain.worktree_changes import (
    MAX_LINES_PER_FILE,
    UnknownRev,
    format_change_comments,
    list_changes,
    read_diff,
)


@pytest.fixture
def repo(tmp_path):
    """A repo on ``feat/work``, branched from a ``main`` that ``origin/main`` matches."""
    setup_git_repo(tmp_path)
    run_git(tmp_path, "checkout", "-b", "main")
    create_commit(tmp_path, "readme.md", "one\ntwo\nthree\n", "chore: start")
    setup_origin_main(tmp_path)
    run_git(tmp_path, "checkout", "-b", "feat/work")
    return tmp_path


def by_path(files):
    return {f["path"]: f for f in files}


# -- uncommitted --------------------------------------------------------------


async def test_staged_unstaged_and_untracked_files_are_dirty_but_env_is_not(repo):
    create_commit(repo, "staged.txt", "a\n", "feat: staged")
    (repo / "staged.txt").write_text("a\nb\n")
    run_git(repo, "add", "staged.txt")
    (repo / "readme.md").write_text("one\nTWO\nthree\n")
    (repo / "new.txt").write_text("fresh\n")
    (repo / ".env").write_text("PORT=3030\n")

    changes = await list_changes(repo)

    assert sorted(changes["dirtyFiles"], key=lambda f: f["path"]) == [
        {"path": "new.txt", "status": "?"},
        {"path": "readme.md", "status": "M"},
        {"path": "staged.txt", "status": "M"},
    ]

    files = by_path(await read_diff(repo, "uncommitted"))
    assert sorted(files) == ["new.txt", "readme.md", "staged.txt"]
    assert files["new.txt"]["status"] == "added"
    assert files["new.txt"]["hunks"][0]["lines"] == [
        {"kind": "add", "text": "fresh", "oldLine": None, "newLine": 1}
    ]
    readme = files["readme.md"]
    assert (readme["status"], readme["additions"], readme["deletions"]) == (
        "modified",
        1,
        1,
    )
    assert readme["hunks"][0]["header"].startswith("@@ -1,3 +1,3 @@")
    assert readme["hunks"][0]["lines"] == [
        {"kind": "context", "text": "one", "oldLine": 1, "newLine": 1},
        {"kind": "remove", "text": "two", "oldLine": 2, "newLine": None},
        {"kind": "add", "text": "TWO", "oldLine": None, "newLine": 2},
        {"kind": "context", "text": "three", "oldLine": 3, "newLine": 3},
    ]


async def test_a_clean_worktree_has_no_uncommitted_diff(repo):
    assert (await list_changes(repo))["dirtyFiles"] == []
    assert await read_diff(repo, "uncommitted") == []


def file_diff(path, **over):
    """A ``FileDiff`` with a plain modified file's defaults."""
    return {
        "path": path,
        "oldPath": None,
        "status": "modified",
        "binary": False,
        "additions": 0,
        "deletions": 0,
        "truncated": False,
        "hunks": [],
        **over,
    }


async def test_a_rename_carries_its_old_path_and_is_one_dirty_file(repo):
    run_git(repo, "mv", "readme.md", "README.md")

    # -z writes the old path as its own entry; it must not read as a second file.
    assert (await list_changes(repo))["dirtyFiles"] == [
        {"path": "README.md", "status": "R"}
    ]
    assert await read_diff(repo, "uncommitted") == [
        file_diff("README.md", oldPath="readme.md", status="renamed")
    ]


async def test_a_deleted_file_shows_its_lines_removed(repo):
    (repo / "readme.md").unlink()

    [deleted] = await read_diff(repo, "uncommitted")

    assert deleted == file_diff(
        "readme.md",
        status="deleted",
        deletions=3,
        hunks=[
            {
                "header": "@@ -1,3 +0,0 @@",
                "lines": [
                    {"kind": "remove", "text": t, "oldLine": n, "newLine": None}
                    for n, t in [(1, "one"), (2, "two"), (3, "three")]
                ],
            }
        ],
    )


async def test_a_binary_file_is_marked_binary_with_no_hunks(repo):
    (repo / "logo.png").write_bytes(b"\x89PNG\x00\x01\x02\x03")

    assert await read_diff(repo, "uncommitted") == [
        file_diff("logo.png", status="added", binary=True)
    ]


async def test_a_path_with_a_space_a_b_slash_and_an_accent_reads_whole(repo):
    (repo / "my b").mkdir()
    create_commit(repo, "my b/café.txt", "old\n", "feat: odd path")
    (repo / "my b" / "café.txt").write_text("new\n")

    [odd] = await read_diff(repo, "uncommitted")

    assert odd["path"] == "my b/café.txt"
    assert (odd["oldPath"], odd["additions"], odd["deletions"]) == (None, 1, 1)


async def test_the_users_diff_config_does_not_change_the_parse(repo):
    run_git(repo, "config", "color.ui", "always")
    run_git(repo, "config", "diff.noprefix", "true")
    run_git(repo, "config", "diff.mnemonicPrefix", "true")
    (repo / "readme.md").write_text("one\nTWO\nthree\n")

    [readme] = await read_diff(repo, "uncommitted")

    assert (readme["path"], readme["additions"], readme["deletions"]) == (
        "readme.md",
        1,
        1,
    )


async def test_a_file_past_the_line_cap_keeps_its_first_lines_and_drops_later_hunks(
    repo,
):
    lines = MAX_LINES_PER_FILE * 2
    create_commit(
        repo, "yarn.lock", "".join(f"line {n}\n" for n in range(lines)), "chore: lock"
    )
    # A rewrite of the first lines past the cap, then an edit far below it, so
    # the diff holds a second hunk the cap must drop.
    body = [f"line {n}\n" for n in range(lines)]
    for n in range(MAX_LINES_PER_FILE):
        body[n] = f"LINE {n}\n"
    body[-2] = "the end\n"
    (repo / "yarn.lock").write_text("".join(body))

    [lock] = await read_diff(repo, "uncommitted")

    assert lock["truncated"] is True
    assert len(lock["hunks"]) == 1
    assert len(lock["hunks"][0]["lines"]) == MAX_LINES_PER_FILE
    assert (lock["additions"], lock["deletions"]) == (
        MAX_LINES_PER_FILE + 1,
        MAX_LINES_PER_FILE + 1,
    )


# -- commits and the base ------------------------------------------------------


async def test_commits_ahead_of_main_list_oldest_first(repo):
    create_commit(repo, "a.txt", "a\n", "feat: first")
    second = create_commit(
        repo,
        "b.txt",
        "b\n",
        "feat: second\n\nWhy it\x1fis needed.\n\nA second paragraph.",
    )

    changes = await list_changes(repo)

    assert changes["base"] == "main"
    assert [
        {k: c[k] for k in ("subject", "body", "author", "filesChanged")}
        for c in changes["commits"]
    ] == [
        {"subject": "feat: first", "body": "", "author": "Test", "filesChanged": 1},
        {
            "subject": "feat: second",
            "body": "Why it\x1fis needed.\n\nA second paragraph.",
            "author": "Test",
            "filesChanged": 1,
        },
    ]
    newest = changes["commits"][-1]
    assert newest["sha"] == second
    assert second.startswith(newest["shortSha"])

    [only] = await read_diff(repo, second)
    assert only["path"] == "b.txt"
    assert sorted(by_path(await read_diff(repo, "branch"))) == ["a.txt", "b.txt"]


async def test_a_stacked_branch_lists_only_its_own_commits(repo):
    create_commit(repo, "parent.txt", "p\n", "feat: parent")
    run_git(repo, "update-ref", "refs/remotes/origin/feat/work", "HEAD")
    run_git(repo, "checkout", "-b", "feat/child")
    run_git(repo, "config", "branch.feat/child.maelBase", "feat/work")
    create_commit(repo, "child.txt", "c\n", "feat: child")

    changes = await list_changes(repo)

    assert changes["base"] == "feat/work"
    assert [c["subject"] for c in changes["commits"]] == ["feat: child"]
    assert sorted(by_path(await read_diff(repo, "branch"))) == ["child.txt"]


async def test_a_pruned_base_falls_back_to_main(repo):
    run_git(repo, "config", "branch.feat/work.maelBase", "feat/gone")
    create_commit(repo, "a.txt", "a\n", "feat: first")

    changes = await list_changes(repo)

    assert changes["base"] == "main"
    assert [c["subject"] for c in changes["commits"]] == ["feat: first"]


async def test_a_sha_outside_the_branch_is_refused(repo):
    on_main = run_git(repo, "rev-parse", "main").stdout.strip()
    create_commit(repo, "a.txt", "a\n", "feat: first")

    with pytest.raises(UnknownRev):
        await read_diff(repo, on_main)


# -- change comments ----------------------------------------------------------


def test_a_range_names_both_ends_and_comments_are_set_apart():
    text = format_change_comments(
        "feat/work",
        [
            make_change_comment(
                endLine=14,
                lines=["+    with open(path) as f:", "+        return f.read()"],
            ),
            make_change_comment(
                path="b.py", startLine=2, endLine=2, lines=[" x"], body="why"
            ),
        ],
    )
    assert text == (
        "Comments on the changes in feat/work, from the orchestrator UI:\n"
        "\n"
        "src/read.py lines 13-14 (uncommitted):\n"
        "> +    with open(path) as f:\n"
        "> +        return f.read()\n"
        "close the file on error too\n"
        "\n"
        "b.py line 2 (uncommitted):\n"
        ">  x\n"
        "why"
    )


def test_an_old_side_range_says_its_numbers_are_the_old_ones():
    text = format_change_comments(
        "feat/work",
        [
            make_change_comment(
                side="old",
                startLine=40,
                endLine=41,
                lines=["-    if x:", "-        return None"],
                body="keep this guard",
            )
        ],
    )
    assert "src/read.py old lines 40-41 (uncommitted):\n" in text


@pytest.mark.parametrize(
    ("rev", "label"),
    [
        ("uncommitted", "(uncommitted)"),
        ("branch", "(branch)"),
        ("0a3e3a3a5c1f4a3b9d0e7f6a5b4c3d2e1f0a9b8c", "(commit 0a3e3a3)"),
    ],
)
def test_each_rev_has_its_label(rev, label):
    text = format_change_comments("feat/work", [make_change_comment(rev=rev)])
    assert f"src/read.py line 13 {label}:\n" in text
