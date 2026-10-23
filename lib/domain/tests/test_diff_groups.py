"""Tests for diff groups: glob matching and grouping a diff's files."""

import pytest

from mael_domain.config import CONFIG_FILENAME, DiffGroup
from mael_domain.diff_groups import glob_matches, group_files, read_diff_groups


@pytest.mark.parametrize(
    ("pattern", "path"),
    [
        ("*.md", "README.md"),
        ("*.md", "docs/guide/concepts.md"),
        ("test_*.py", "lib/domain/tests/test_config.py"),
        ("docs/**", "docs/guide/concepts.md"),
        ("**/tests/**", "lib/domain/tests/test_config.py"),
        ("**/tests/**", "tests/test_x.py"),
        ("src/*.ts", "src/app.ts"),
        ("src/**/*.ts", "src/app.ts"),
        ("src/**/*.ts", "src/a/b/app.ts"),
        ("file?.txt", "file1.txt"),
        ("uv.lock", "uv.lock"),
        # A leading `/` anchors at the root; a trailing `/` names a directory.
        ("/uv.lock", "uv.lock"),
        ("docs/", "docs/guide/concepts.md"),
    ],
)
def test_glob_matches(pattern, path):
    assert glob_matches(pattern, path)


@pytest.mark.parametrize(
    ("pattern", "path"),
    [
        ("*.md", "README.mdx"),
        ("docs/**", "lib/docs/x.md"),
        ("src/*.ts", "src/a/app.ts"),
        ("file?.txt", "file12.txt"),
        ("**/tests/**", "lib/testsuite/x.py"),
        ("a.b", "axb"),
        ("/uv.lock", "web/uv.lock"),
        ("docs/", "lib/docs/x.md"),
    ],
)
def test_glob_does_not_match(pattern, path):
    assert not glob_matches(pattern, path)


GROUPS = [
    DiffGroup(name="Tests", paths=["**/tests/**", "*.test.ts"]),
    DiffGroup(name="Docs", paths=["docs/**", "*.md"]),
    DiffGroup(name="Lockfiles", paths=["uv.lock"], collapsed=True),
]


def test_the_first_group_that_matches_wins():
    # docs/tests/x.md matches both Tests and Docs; Tests is first.
    assert group_files(["docs/tests/x.md"], GROUPS) == [
        {"name": "Tests", "collapsed": False, "paths": ["docs/tests/x.md"]},
    ]


def test_groups_keep_config_order_and_other_comes_last():
    paths = ["src/app.py", "uv.lock", "README.md", "web/a.test.ts", "src/b.py"]
    assert group_files(paths, GROUPS) == [
        {"name": "Tests", "collapsed": False, "paths": ["web/a.test.ts"]},
        {"name": "Docs", "collapsed": False, "paths": ["README.md"]},
        {"name": "Lockfiles", "collapsed": True, "paths": ["uv.lock"]},
        {"name": "Other", "collapsed": False, "paths": ["src/app.py", "src/b.py"]},
    ]


def test_with_no_groups_every_file_is_in_other():
    assert group_files(["a.py", "b.md"], []) == [
        {"name": "Other", "collapsed": False, "paths": ["a.py", "b.md"]},
    ]


def test_an_empty_diff_has_no_groups():
    assert group_files([], GROUPS) == []


def test_a_group_with_no_files_is_dropped():
    assert [g["name"] for g in group_files(["README.md"], GROUPS)] == ["Docs"]


def test_a_group_named_other_matches_at_its_place_and_draws_last():
    # README.md matches Other's glob before Docs', so Other takes it.
    groups = [DiffGroup(name="Other", paths=["README.md"], collapsed=True), *GROUPS]
    assert group_files(["README.md", "docs/a.md", "x.txt"], groups) == [
        {"name": "Docs", "collapsed": False, "paths": ["docs/a.md"]},
        {"name": "Other", "collapsed": True, "paths": ["README.md", "x.txt"]},
    ]


@pytest.mark.parametrize(
    "config",
    [
        None,
        "diff_groups: [\n",
        "- a list, not a map\n",
        "services: [web]\n",
        "services:\n  web: {command: x, env: 5}\n",
    ],
)
def test_a_config_that_does_not_load_gives_no_groups(tmp_path, config):
    if config is not None:
        (tmp_path / CONFIG_FILENAME).write_text(config)
    assert read_diff_groups(tmp_path) == []


def test_read_diff_groups_reads_the_worktree_config(tmp_path):
    (tmp_path / CONFIG_FILENAME).write_text(
        "diff_groups:\n  - {name: Docs, paths: ['*.md'], collapsed: true}\n"
    )
    assert read_diff_groups(tmp_path) == [
        DiffGroup(name="Docs", paths=["*.md"], collapsed=True)
    ]
