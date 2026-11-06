"""Tests for the wiki model, over both table backends.

The model is pure — the table is injected — so each test runs once over the
in-memory table and once over the SQLite table.
"""

import pytest

from mael_domain import wiki
from mael_domain.state_db.migrations.wiki import is_page_id


@pytest.fixture(params=["memory", "sqlite"])
def table(request) -> wiki.WikiTable:
    if request.param == "memory":
        return wiki.InMemoryWikiTable()
    return wiki.SqliteWikiTable(request.getfixturevalue("state_db"))


PAGE = """---
description: How to publish a package to PyPI
---

# PyPI publication

Use trusted publishing.
"""


# --- path handling ---


@pytest.mark.parametrize(
    "given,expected",
    [
        ("dev-patterns/python/pypi", "dev-patterns/python/pypi"),
        ("dev-patterns/python/pypi.md", "dev-patterns/python/pypi"),
        ("/dev-patterns/python/pypi", "dev-patterns/python/pypi"),
        ("  linting  ", "linting"),
    ],
)
def test_normalise_page_accepts_safe_paths(given, expected):
    assert wiki.normalise_page(given) == expected


@pytest.mark.parametrize(
    "given",
    ["", "   ", "/", "..", "../escape", "dev/../../etc/passwd", "a//b", "a b/c"],
)
def test_normalise_page_rejects_unsafe_paths(given):
    with pytest.raises(ValueError):
        wiki.normalise_page(given)


@pytest.mark.parametrize(
    "page",
    [
        "linting",
        "dev-patterns/python/pypi",
        "a.b",
        "a.md",
        "x.md/y",
        ".hidden",
        "",
        "a b",
        ".",
        "..",
        "a/../b",
        "a//b",
        "a/./b",
        "a\tb",
        "dev/*",
    ],
)
def test_the_import_rung_agrees_with_normalise_page(page):
    """The rung's page-id rule is a copy, so it must not drift from the model's.

    A legal id is one that ``normalise_page`` can produce: ``a.md`` is the id
    of the page written as ``a.md.md``.
    """
    try:
        legal = wiki.normalise_page(page + ".md") == page
    except ValueError:
        legal = False
    assert is_page_id(page) == legal


# --- description parsing ---


def test_parse_description_reads_the_frontmatter_line():
    assert wiki.parse_description(PAGE) == "How to publish a package to PyPI"


def test_parse_description_strips_quotes():
    text = '---\ndescription: "Quoted value"\n---\n\nBody\n'
    assert wiki.parse_description(text) == "Quoted value"


@pytest.mark.parametrize(
    "text",
    [
        "# No frontmatter\n\nBody\n",
        "---\ntitle: Something else\n---\n\nBody\n",
        "",
        "---\nunterminated: yes\n",
    ],
)
def test_parse_description_is_empty_without_a_description(text):
    assert wiki.parse_description(text) == ""


# --- read / write ---


async def test_write_then_read_round_trips(table):
    await wiki.write_page(table, "dev-patterns/python/pypi", PAGE)
    assert await wiki.read_page(table, "dev-patterns/python/pypi") == PAGE


async def test_read_page_accepts_the_md_suffix(table):
    await wiki.write_page(table, "linting", PAGE)
    assert await wiki.read_page(table, "linting.md") == PAGE


async def test_read_page_raises_key_error_when_missing(table):
    with pytest.raises(KeyError):
        await wiki.read_page(table, "nope")


async def test_read_page_raises_value_error_on_an_unsafe_path(table):
    with pytest.raises(ValueError):
        await wiki.read_page(table, "../escape")


async def test_write_page_raises_value_error_on_an_unsafe_path(table):
    with pytest.raises(ValueError):
        await wiki.write_page(table, "../escape", PAGE)
    assert await table.list() == {}


async def test_write_page_returns_the_normalised_id(table):
    assert await wiki.write_page(table, "/linting.md", PAGE) == "linting"


async def test_write_page_replaces_the_whole_body(table):
    await wiki.write_page(table, "linting", "old\n")
    await wiki.write_page(table, "linting", "new\n")
    assert await wiki.read_page(table, "linting") == "new\n"


async def test_write_page_adds_a_trailing_newline(table):
    await wiki.write_page(table, "linting", "no trailing newline")
    assert await wiki.read_page(table, "linting") == "no trailing newline\n"


async def test_write_page_leaves_empty_content_empty(table):
    await wiki.write_page(table, "linting", "")
    assert await wiki.read_page(table, "linting") == ""


# --- listing ---


async def test_list_pages_is_empty_for_a_fresh_table(table):
    assert await wiki.list_pages(table) == []


async def test_list_pages_returns_paths_and_descriptions_sorted(table):
    await wiki.write_page(table, "linting", "# Linting\n")
    await wiki.write_page(table, "dev-patterns/python/pypi", PAGE)
    assert await wiki.list_pages(table) == [
        wiki.WikiPage(
            path="dev-patterns/python/pypi",
            description="How to publish a package to PyPI",
        ),
        wiki.WikiPage(path="linting", description=""),
    ]
