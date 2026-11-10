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
    FileTooLarge,
    NoSuchFile,
    UnknownRev,
    differing_paths,
    format_change_comments,
    in_rev,
    list_changes,
    list_files,
    new_commit,
    read_diff,
    read_excerpt,
    read_file_bytes,
    read_file_versions,
    rev_text,
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
    assert readme["hunks"][0]["range"] == {"start": 1, "end": 3}
    assert files["new.txt"]["hunks"][0]["range"] == {"start": 1, "end": 1}
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
        "shebang": None,
        "navigable": False,
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
                # No new lines: an empty range, which ends before it starts.
                "range": {"start": 1, "end": 0},
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


SCRIPT = "#!/usr/bin/env bash\n" + "".join(f"echo {n}\n" for n in range(20))
EDIT = SCRIPT.replace("echo 15", "echo fifteen")


@pytest.fixture
def scripts(repo):
    """A branch commit that edits each kind of file below line 1; returns its sha."""
    (repo / "bin").mkdir()
    for name, text in [
        ("bin/lint", SCRIPT),
        (".hook", SCRIPT),
        ("bin/plain", "no shebang\n" * 20),
        ("tool.py", SCRIPT),
    ]:
        create_commit(repo, name, text, f"feat: {name}")
    for name in ["bin/lint", ".hook", "tool.py"]:
        (repo / name).write_text(EDIT)
    (repo / "bin/plain").write_text("no shebang\n" * 19 + "changed\n")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-q", "-m", "feat: edit")
    return run_git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.mark.parametrize("rev", ["commit", "branch", "uncommitted"])
async def test_a_file_with_no_extension_carries_its_shebang(repo, scripts, rev):
    if rev == "uncommitted":
        for name in ["bin/lint", ".hook", "bin/plain", "tool.py"]:
            (repo / name).write_text((repo / name).read_text() + "echo more\n")

    files = by_path(await read_diff(repo, scripts if rev == "commit" else rev))

    assert {p: f["shebang"] for p, f in files.items()} == {
        "bin/lint": "#!/usr/bin/env bash",
        ".hook": "#!/usr/bin/env bash",
        "bin/plain": None,
        "tool.py": None,
    }


async def test_a_deleted_script_carries_the_shebang_of_its_old_side(repo, scripts):
    run_git(repo, "rm", "-q", "bin/lint")
    run_git(repo, "commit", "-q", "-m", "feat: drop lint")
    sha = run_git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / ".hook").unlink()

    [committed] = await read_diff(repo, sha)
    [uncommitted] = await read_diff(repo, "uncommitted")

    assert (committed["status"], committed["shebang"]) == (
        "deleted",
        "#!/usr/bin/env bash",
    )
    assert (uncommitted["status"], uncommitted["shebang"]) == (
        "deleted",
        "#!/usr/bin/env bash",
    )


# -- commits --------------------------------------------------------------------


async def test_commits_ahead_of_main_list_oldest_first(repo):
    create_commit(repo, "a.txt", "a\n", "feat: first")
    second = create_commit(
        repo,
        "b.txt",
        "b\n",
        "feat: second\n\nWhy it\x1fis needed.\n\nA second paragraph.",
    )

    changes = await list_changes(repo)

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


async def test_a_sha_outside_the_branch_is_refused(repo):
    on_main = run_git(repo, "rev-parse", "main").stdout.strip()
    create_commit(repo, "a.txt", "a\n", "feat: first")

    with pytest.raises(UnknownRev):
        await read_diff(repo, on_main)


# -- file list ----------------------------------------------------------------


async def test_the_uncommitted_list_is_the_working_tree_less_ignored_files(repo):
    create_commit(repo, "gone.txt", "g\n", "feat: gone")
    (repo / "gone.txt").unlink()
    (repo / ".gitignore").write_text("*.log\n")
    (repo / "new.txt").write_text("fresh\n")
    (repo / "debug.log").write_text("noise\n")

    assert await list_files(repo, "uncommitted") == {
        "rev": "uncommitted",
        "paths": [".gitignore", "new.txt", "readme.md"],
    }


async def test_the_branch_lists_heads_tree_and_a_commit_lists_its_own(repo):
    (repo / "src").mkdir()
    first = create_commit(repo, "src/a.py", "a\n", "feat: a")
    create_commit(repo, "src/b.py", "b\n", "feat: b")
    (repo / "dirty.txt").write_text("not committed\n")

    assert (await list_files(repo, "branch"))["paths"] == [
        "readme.md",
        "src/a.py",
        "src/b.py",
    ]
    assert (await list_files(repo, first))["paths"] == ["readme.md", "src/a.py"]


async def test_a_sha_outside_the_branch_has_no_file_list(repo):
    on_main = run_git(repo, "rev-parse", "main").stdout.strip()

    with pytest.raises(UnknownRev):
        await list_files(repo, on_main)


# -- what a language server reads -------------------------------------------


async def test_the_rev_text_of_each_file_that_differs_from_the_working_tree(repo):
    sha = create_commit(repo, "a.py", "A = 1\n", "feat: a")
    create_commit(repo, "b.py", "B = 1\n", "feat: b")
    (repo / "a.py").write_text("A = 2\n")

    assert (
        await new_commit(repo, "branch")
        == run_git(repo, "rev-parse", "HEAD").stdout.strip()
    )
    assert await new_commit(repo, "uncommitted") is None
    # At the first commit, b.py is on the disk but not in the commit.
    assert await differing_paths(repo, sha) == {"a.py", "b.py"}
    assert await differing_paths(repo, None) == set()
    assert [await rev_text(repo, sha, name) for name in ("a.py", "b.py")] == [
        "A = 1\n",
        None,
    ]
    assert await rev_text(repo, None, "a.py") == "A = 2\n"


async def test_a_rev_holds_its_tracked_files_and_the_working_tree_its_listed_ones(
    repo,
):
    sha = create_commit(repo, ".gitignore", ".venv/\n", "chore: ignore")
    (repo / ".venv").mkdir()
    (repo / ".venv/site.py").write_text("x = 1\n")
    (repo / "new.py").write_text("y = 1\n")

    names = ("readme.md", "new.py", ".venv/site.py", "absent.py")
    assert [await in_rev(repo, sha, n) for n in names] == [True, False, False, False]
    # An ignored file is on the disk but in no rev.
    assert [await in_rev(repo, None, n) for n in names] == [True, True, False, False]


# -- excerpts -----------------------------------------------------------------


def context(numbers, texts):
    return [
        {"kind": "context", "text": t, "oldLine": n, "newLine": n}
        for n, t in zip(numbers, texts, strict=True)
    ]


async def test_an_excerpt_reads_a_range_of_the_new_side(repo):
    create_commit(repo, "a.py", "".join(f"l{n}\n" for n in range(1, 11)), "feat: a")
    (repo / "a.py").write_text("dirty\n")

    excerpt = await read_excerpt(repo, "branch", "a.py", 3, 5)

    assert excerpt == {
        "rev": "branch",
        "path": "a.py",
        "navigable": False,
        "total": 10,
        "truncated": False,
        "span": {
            "range": {"start": 3, "end": 5},
            "lines": context([3, 4, 5], ["l3", "l4", "l5"]),
        },
    }


async def test_an_excerpt_with_no_range_is_the_whole_working_file(repo):
    (repo / "readme.md").write_text("one\nTWO\n")

    excerpt = await read_excerpt(repo, "uncommitted", "readme.md")

    assert (excerpt["total"], excerpt["span"]) == (
        2,
        {"range": {"start": 1, "end": 2}, "lines": context([1, 2], ["one", "TWO"])},
    )


async def test_an_excerpt_range_is_clipped_to_the_file(repo):
    excerpt = await read_excerpt(repo, "uncommitted", "readme.md", 2, 99)

    assert excerpt["span"]["range"] == {"start": 2, "end": 3}


async def test_an_excerpt_past_the_line_cap_keeps_its_first_lines(repo):
    lines = MAX_LINES_PER_FILE + 10
    (repo / "big.txt").write_text("".join(f"{n}\n" for n in range(1, lines + 1)))

    excerpt = await read_excerpt(repo, "uncommitted", "big.txt")

    assert (excerpt["total"], excerpt["truncated"]) == (lines, True)
    assert excerpt["span"]["range"] == {"start": 1, "end": MAX_LINES_PER_FILE}
    assert len(excerpt["span"]["lines"]) == MAX_LINES_PER_FILE


async def test_a_binary_or_oversized_file_has_an_excerpt_with_no_lines(repo):
    create_commit(repo, "image.bin", "\x89PNG\x00\x01", "feat: image")
    (repo / "big.txt").write_text("x" * (MAX_FILE_BYTES + 1))

    binary = await read_excerpt(repo, "branch", "image.bin")
    big = await read_excerpt(repo, "uncommitted", "big.txt")

    assert (binary["total"], binary["truncated"], binary["span"]["lines"]) == (
        0,
        False,
        [],
    )
    assert (big["total"], big["truncated"], big["span"]["lines"]) == (0, True, [])


async def test_a_path_missing_at_the_rev_has_no_excerpt(repo, tmp_path_factory):
    (repo / "docs").mkdir()
    first = create_commit(repo, "docs/a.py", "a\n", "feat: a")
    create_commit(repo, "b.py", "b\n", "feat: b")
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret").write_text("secret\n")
    (repo / "dir").symlink_to(outside)

    for rev, file in [
        (first, "b.py"),
        ("branch", "docs"),
        ("uncommitted", "c.py"),
        ("uncommitted", "dir/secret"),
    ]:
        with pytest.raises(NoSuchFile):
            await read_excerpt(repo, rev, file)
    with pytest.raises(BadPath):
        await read_excerpt(repo, "branch", "../outside.py")


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


# -- file bytes ---------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n\x00\xff"


def commit_bytes(repo, name, data, message):
    (repo / name).write_bytes(data)
    run_git(repo, "add", name)
    run_git(repo, "commit", "-qm", message)
    return run_git(repo, "rev-parse", "HEAD").stdout.strip()


async def test_each_side_of_each_rev_reads_its_bytes_unchanged(repo):
    commit_bytes(repo, "shot.png", PNG + b"base", "feat: shot")
    commit = commit_bytes(repo, "shot.png", PNG + b"head", "feat: retake")
    (repo / "shot.png").write_bytes(PNG + b"dirty")

    sides = {
        (rev, side): await read_file_bytes(repo, rev, "shot.png", side)
        for rev in ("uncommitted", "branch", commit)
        for side in ("old", "new")
    }

    assert sides == {
        ("uncommitted", "old"): PNG + b"head",
        ("uncommitted", "new"): PNG + b"dirty",
        ("branch", "old"): None,
        ("branch", "new"): PNG + b"head",
        (commit, "old"): PNG + b"base",
        (commit, "new"): PNG + b"head",
    }


async def test_a_renamed_file_reads_its_old_side_at_the_old_path(repo):
    run_git(repo, "mv", "readme.md", "README.md")

    old = await read_file_bytes(
        repo, "uncommitted", "README.md", "old", old_file="readme.md"
    )

    assert old == b"one\ntwo\nthree\n"


async def test_a_side_past_the_raw_cap_is_refused_and_one_at_it_is_served(
    repo, monkeypatch
):
    monkeypatch.setattr("mael_domain.worktree_changes.MAX_RAW_BYTES", 16)
    commit_bytes(repo, "at.png", b"x" * 16, "feat: at the cap")
    commit_bytes(repo, "over.png", b"x" * 17, "feat: over the cap")
    (repo / "dirty.png").write_bytes(b"x" * 17)

    assert await read_file_bytes(repo, "branch", "at.png", "new") == b"x" * 16
    with pytest.raises(FileTooLarge):
        await read_file_bytes(repo, "branch", "over.png", "new")
    with pytest.raises(FileTooLarge):
        await read_file_bytes(repo, "uncommitted", "dirty.png", "new")


@pytest.mark.parametrize("bad", ["../outside.png", "/etc/passwd", "a/../../b"])
async def test_a_bytes_path_outside_the_worktree_is_refused(repo, bad):
    with pytest.raises(BadPath):
        await read_file_bytes(repo, "uncommitted", bad, "new")
    with pytest.raises(BadPath):
        await read_file_bytes(repo, "uncommitted", "readme.md", "old", old_file=bad)


async def test_a_bytes_read_never_follows_a_symlink_out(repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret.png").write_bytes(PNG)
    (repo / "link.png").symlink_to(outside / "secret.png")
    (repo / "dir").symlink_to(outside)

    assert await read_file_bytes(repo, "uncommitted", "link.png", "new") is None
    assert await read_file_bytes(repo, "uncommitted", "dir/secret.png", "new") is None
    assert await read_file_bytes(repo, "uncommitted", "readme.md/x", "new") is None


async def test_a_path_that_names_a_folder_has_no_bytes(repo):
    (repo / "docs").mkdir()
    create_commit(repo, "docs/guide.md", "g\n", "feat: guide")

    assert await read_file_bytes(repo, "branch", "docs", "new") is None


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
