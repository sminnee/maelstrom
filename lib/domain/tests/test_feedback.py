"""The messages that carry a jig's **Feedback** to the agents of a worktree."""

import pytest

from mael_domain.feedback import format_monkeypatch


def test_a_monkeypatch_is_quoted_as_one_block_with_its_note():
    text = format_monkeypatch(
        "feat/orders", ".chip { padding: 14px; }\n\n.row { gap: 4px; }\n", " tighter "
    )

    assert text == (
        "Monkeypatch for feat/orders, from the jig (.drafts/monkeypatch.css):\n"
        "\n"
        "    .chip { padding: 14px; }\n"
        "\n"
        "    .row { gap: 4px; }\n"
        "\n"
        "Note: tighter\n"
        "Apply it in source, then delete .drafts/monkeypatch.css."
    )


@pytest.mark.parametrize("note", [None, "", "  "], ids=["absent", "empty", "blank"])
def test_a_monkeypatch_without_a_note_omits_the_note_line(note):
    """The jig sends an empty note field as ``""``, not as no key."""
    assert format_monkeypatch("feat/orders", ".chip{padding:20px}", note) == (
        "Monkeypatch for feat/orders, from the jig (.drafts/monkeypatch.css):\n"
        "\n"
        "    .chip{padding:20px}\n"
        "\n"
        "Apply it in source, then delete .drafts/monkeypatch.css."
    )
