"""Tests for the wiki model, against an InMemoryStore.

The model is pure — the store is injected — so every test here runs with no git
and no filesystem.
"""

import pytest

from maelstrom import wiki
from maelstrom.task_store import InMemoryStore

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


def test_page_key_is_under_the_wiki_prefix():
    assert (
        wiki.page_key("dev-patterns/python/pypi") == "_wiki/dev-patterns/python/pypi.md"
    )


def test_the_wiki_prefix_is_a_reserved_project_name():
    """Task keys are ``<project>/<status>/<id>.md``, so the prefix must be reserved.

    Without this, a project named ``_wiki`` would write its tasks into the wiki's
    key space and they would list as pages. The leading underscore alone does not
    prevent that — the reserved-name check does.
    """
    from maelstrom.context import RESERVED_PROJECT_NAMES

    assert wiki.WIKI_PREFIX.rstrip("/") in RESERVED_PROJECT_NAMES


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


def test_write_then_read_round_trips(store: InMemoryStore):
    wiki.write_page(store, "dev-patterns/python/pypi", PAGE)
    assert wiki.read_page(store, "dev-patterns/python/pypi") == PAGE


def test_read_page_accepts_the_md_suffix(store: InMemoryStore):
    wiki.write_page(store, "linting", PAGE)
    assert wiki.read_page(store, "linting.md") == PAGE


def test_read_page_raises_key_error_when_missing(store: InMemoryStore):
    with pytest.raises(KeyError):
        wiki.read_page(store, "nope")


def test_read_page_raises_value_error_on_an_unsafe_path(store: InMemoryStore):
    with pytest.raises(ValueError):
        wiki.read_page(store, "../escape")


def test_write_page_returns_the_normalised_id(store: InMemoryStore):
    assert wiki.write_page(store, "/linting.md", PAGE) == "linting"


def test_write_page_replaces_the_whole_body(store: InMemoryStore):
    wiki.write_page(store, "linting", "old\n")
    wiki.write_page(store, "linting", "new\n")
    assert wiki.read_page(store, "linting") == "new\n"


def test_write_page_adds_a_trailing_newline(store: InMemoryStore):
    wiki.write_page(store, "linting", "no trailing newline")
    assert wiki.read_page(store, "linting") == "no trailing newline\n"


def test_write_page_leaves_empty_content_empty(store: InMemoryStore):
    wiki.write_page(store, "linting", "")
    assert wiki.read_page(store, "linting") == ""


class _RecordingStore(InMemoryStore):
    """An in-memory store that records the commit message of each write."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str | None] = []

    def write(self, key: str, text: str, *, message: str | None = None) -> None:
        self.messages.append(message)
        super().write(key, text, message=message)


def test_write_page_commit_message_distinguishes_create_from_update():
    store = _RecordingStore()
    wiki.write_page(store, "linting", "a\n")
    wiki.write_page(store, "linting", "b\n")
    assert store.messages == ["wiki: create linting", "wiki: update linting"]


# --- listing ---


def test_list_pages_is_empty_for_a_fresh_store(store: InMemoryStore):
    assert wiki.list_pages(store) == []


def test_list_pages_returns_paths_and_descriptions(store: InMemoryStore):
    wiki.write_page(store, "dev-patterns/python/pypi", PAGE)
    wiki.write_page(store, "linting", "# Linting\n")
    assert wiki.list_pages(store) == [
        wiki.WikiPage(
            path="dev-patterns/python/pypi",
            description="How to publish a package to PyPI",
        ),
        wiki.WikiPage(path="linting", description=""),
    ]


def test_list_pages_ignores_non_markdown_keys(store: InMemoryStore):
    wiki.write_page(store, "linting", PAGE)
    store.write("_wiki/notes.txt", "not a page")
    assert [p.path for p in wiki.list_pages(store)] == ["linting"]


def test_list_pages_ignores_keys_that_are_not_legal_page_ids(store: InMemoryStore):
    """The store is a real directory, so a key under the prefix may not be a page.

    A file placed by hand (or by another tool) can carry a name no page id can
    round-trip to. Listing it would advertise a page that cannot be read.
    """
    wiki.write_page(store, "linting", PAGE)
    store.write("_wiki/a b.md", PAGE)
    store.write("_wiki/.md", PAGE)
    store.write("_wiki/../escaped.md", PAGE)
    assert [p.path for p in wiki.list_pages(store)] == ["linting"]


def test_every_listed_page_can_be_read(store: InMemoryStore):
    """The TOC must not advertise a page that ``read_page`` cannot resolve."""
    wiki.write_page(store, "dev-patterns/python/pypi", PAGE)
    store.write("_wiki/a b.md", PAGE)
    store.write("_wiki/.md", PAGE)
    for page in wiki.list_pages(store):
        assert wiki.read_page(store, page.path) is not None


def test_list_pages_ignores_task_keys(store: InMemoryStore):
    """Tasks share the store; they must not appear in the table of contents."""
    store.write("myproject/todo/1.md", "---\ntitle: A task\n---\n")
    wiki.write_page(store, "linting", PAGE)
    assert [p.path for p in wiki.list_pages(store)] == ["linting"]
