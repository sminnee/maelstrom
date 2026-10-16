"""A worktree's changes, read from real repositories.

Every case builds a repo with :mod:`git_helpers`, because the reads are a parse
of git's own output: a faked ``git diff`` would test the fake.
"""

import pytest
from agent_fixtures import make_change_comment
from git_helpers import create_commit, run_git, setup_git_repo, setup_origin_main

from mael_domain.worktree_changes import (
    MAX_FILE_BYTES,
    MAX_LINES_PER_FILE,
    BadPath,
    UnknownRev,
    format_change_comments,
    list_changes,
    read_diff,
    read_file_versions,
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


async def test_untracked_files_keep_their_ls_files_order(repo):
    # More files than are diffed at once, and the first is the slowest to
    # parse, so an order of finishing would differ from the path order.
    (repo / "a.txt").write_text("line\n" * 100_000)
    for name in "bcdef":
        (repo / f"{name}.txt").write_text("x\n")

    files = await read_diff(repo, "uncommitted")

    assert [f["path"] for f in files] == [
        "a.txt",
        "b.txt",
        "c.txt",
        "d.txt",
        "e.txt",
        "f.txt",
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


# -- file versions ------------------------------------------------------------


async def test_uncommitted_versions_are_head_and_the_worktree_file(repo):
    (repo / "readme.md").write_text("one\nTWO\nthree\n")

    assert await read_file_versions(repo, "uncommitted", "readme.md") == {
        "rev": "uncommitted",
        "path": "readme.md",
        "old": "one\ntwo\nthree\n",
        "new": "one\nTWO\nthree\n",
        "truncated": False,
    }


async def test_branch_versions_are_the_merge_base_and_head(repo):
    create_commit(repo, "readme.md", "one\n", "feat: shorten")
    create_commit(repo, "added.txt", "a\n", "feat: add")
    (repo / "readme.md").write_text("dirty\n")

    readme = await read_file_versions(repo, "branch", "readme.md")
    added = await read_file_versions(repo, "branch", "added.txt")

    assert (readme["old"], readme["new"]) == ("one\ntwo\nthree\n", "one\n")
    assert (added["old"], added["new"]) == (None, "a\n")


async def test_commit_versions_are_its_parent_and_itself(repo):
    create_commit(repo, "readme.md", "one\n", "feat: shorten")
    second = create_commit(repo, "readme.md", "uno\n", "feat: translate")

    versions = await read_file_versions(repo, second, "readme.md")

    assert (versions["old"], versions["new"]) == ("one\n", "uno\n")


async def test_a_deleted_file_has_no_new_side_and_a_rename_reads_its_old_path(repo):
    create_commit(repo, "gone.txt", "g\n", "feat: gone")
    run_git(repo, "rm", "-q", "gone.txt")
    run_git(repo, "mv", "readme.md", "README.md")

    renamed = await read_file_versions(
        repo, "uncommitted", "README.md", old_file="readme.md"
    )
    deleted = await read_file_versions(repo, "uncommitted", "gone.txt")

    assert (renamed["old"], renamed["new"]) == ("one\ntwo\nthree\n",) * 2
    assert (deleted["old"], deleted["new"]) == ("g\n", None)


async def test_a_sha_outside_the_branch_has_no_versions(repo):
    on_main = run_git(repo, "rev-parse", "main").stdout.strip()

    with pytest.raises(UnknownRev):
        await read_file_versions(repo, on_main, "readme.md")


@pytest.mark.parametrize("bad", ["../outside.txt", "/etc/passwd", "a/../../b"])
async def test_a_path_outside_the_worktree_is_refused(repo, bad):
    with pytest.raises(BadPath):
        await read_file_versions(repo, "uncommitted", bad)
    with pytest.raises(BadPath):
        await read_file_versions(repo, "uncommitted", "readme.md", old_file=bad)


async def test_a_side_past_the_size_cap_or_binary_drops_its_text(repo):
    create_commit(repo, "image.bin", "\x89PNG\x00\x01", "feat: image")
    create_commit(repo, "big.txt", "x" * (MAX_FILE_BYTES + 1), "feat: big")
    (repo / "readme.md").write_text("x" * (MAX_FILE_BYTES + 1))

    big = await read_file_versions(repo, "uncommitted", "readme.md")
    committed_big = await read_file_versions(repo, "branch", "big.txt")
    binary = await read_file_versions(repo, "branch", "image.bin")

    assert (big["old"], big["new"], big["truncated"]) == (
        "one\ntwo\nthree\n",
        None,
        True,
    )
    assert (committed_big["new"], committed_big["truncated"]) == (None, True)
    assert (binary["old"], binary["new"], binary["truncated"]) == (None, None, False)


async def test_a_symlink_reads_as_its_target_and_is_never_followed_out(
    repo, tmp_path_factory
):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret").write_text("secret\n")
    (repo / "link").symlink_to(outside / "secret")
    (repo / "dir").symlink_to(outside)

    link = await read_file_versions(repo, "uncommitted", "link")
    through_dir = await read_file_versions(repo, "uncommitted", "dir/secret")
    under_a_file = await read_file_versions(repo, "uncommitted", "readme.md/x")

    assert link["new"] == str(outside / "secret")
    assert through_dir["new"] is None
    assert under_a_file["new"] is None


# -- change comments ----------------------------------------------------------


def test_a_range_names_both_ends_and_comments_are_set_apart():
    text = format_change_comments(
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
        "src/read.py lines 13-14 (uncommitted):\n"
        "> +    with open(path) as f:\n"
        "> +        return f.read()\n"
        "\n"
        "close the file on error too\n"
        "\n"
        "b.py line 2 (uncommitted):\n"
        ">  x\n"
        "\n"
        "why"
    )


def test_an_old_side_range_says_its_numbers_are_the_old_ones():
    text = format_change_comments(
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
    text = format_change_comments([make_change_comment(rev=rev)])
    assert f"src/read.py line 13 {label}:\n" in text
