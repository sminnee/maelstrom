"""The media a document body names: which refs are files in the worktree."""

import pytest

from mael_domain.document_tags import media_refs, partial_text, replace_media


def targets(markdown: str) -> list[str]:
    return [ref.target for ref in media_refs(markdown)]


def test_a_relative_ref_is_found_with_its_alt():
    [ref] = media_refs("Before.\n\n![Login flow](test-results/login/video.webm)\n")

    assert (ref.alt, ref.target) == ("Login flow", "test-results/login/video.webm")


def test_a_ref_with_a_title_is_found_and_an_escaped_bracket_is_part_of_the_alt():
    [titled, escaped] = media_refs('![a](one.png "The title") ![b \\] c](two.png)')

    assert (titled.alt, titled.target) == ("a", "one.png")
    assert (escaped.alt, escaped.target) == ("b ] c", "two.png")


def test_refs_come_out_in_the_order_they_were_written():
    body = "![a](one.png) and ![b](./shots/two.png)"

    assert targets(body) == ["one.png", "./shots/two.png"]


def test_a_url_is_not_a_worktree_file():
    body = (
        "![a](https://example.com/a.png) ![b](http://localhost:3000/b.png) "
        "![c](data:image/png;base64,AAAA) ![d](//cdn.example.com/d.png)"
    )

    assert targets(body) == []


def test_an_absolute_path_and_a_served_route_are_left_alone():
    body = "![a](/etc/passwd) ![b](/api/files/ag1-3-shot.png) ![c](~/shot.png)"

    assert targets(body) == []


def test_a_notebook_token_is_already_attached():
    assert targets("![a]({{MAEL_TASK_DIR}}/images/NORT-7/a.png)") == []


def test_a_ref_in_a_code_fence_is_left_alone():
    body = (
        "```markdown\n![a](one.png)\n```\n\n~~~\n![b](two.png)\n~~~\n\n![c](three.png)"
    )

    assert targets(body) == ["three.png"]


def test_a_ref_in_inline_code_is_left_alone():
    assert targets("Write `![a](one.png)` to show it. ![b](two.png)") == ["two.png"]


def test_a_link_is_not_media():
    assert targets("See [the notes](docs/notes.md).") == []


def test_replace_media_rewrites_the_target_and_keeps_the_prose():
    body = "Before ![a b](one.png) after ![c](two.webm)."

    out = replace_media(body, lambda ref: f"<{ref.target}|{ref.alt}>")

    assert out == "Before <one.png|a b> after <two.webm|c>."


def test_replace_media_leaves_a_fenced_ref_as_written():
    body = "```\n![a](one.png)\n```"

    assert replace_media(body, lambda ref: "gone") == body


# --- a partial message: no tag shows before the message is complete ---------


@pytest.mark.parametrize(
    ("so_far", "shown"),
    [
        # A complete marker is cut, as it is from the complete message.
        ("<note>rebasing</note>\n\nThe rebase", "The rebase"),
        (
            'Done.\n\n<doc-file kind="pr" filename=".drafts/pr.md">\n\nNext',
            "Done.\n\nNext",
        ),
        ("Built.\n\n<milestone>built</milestone>", "Built."),
        ('See <image src="docs/shot.png" alt="A"> here', "See  here"),
        # A marker's body is not prose, so nothing shows until it closes.
        ("Before\n\n<note>rebasing on", "Before"),
        ("Before\n\n<milestone>bui", "Before"),
        # A half-written tag is held back, whatever it will turn out to be.
        ("Before\n\n<", "Before"),
        ("Before\n\n<doc-", "Before"),
        ('Before\n\n<doc-file kind="pr" filename=".drafts', "Before"),
        ("Before\n\n<user-attention lo", "Before"),
        ("Before\n\n</no", "Before"),
        # The renderer reads this one, so it stays once it is whole.
        ("<user-attention high>\nHello", "<user-attention high>\nHello"),
        # Prose that is not a marker is not held back.
        ("if a < b and c", "if a < b and c"),
        ("a <b>bold", "a <b>bold"),
        ("a <div class", "a <div class"),
        # An agent quoting a marker in code is writing about it, not using it.
        ("Write a `<note>` then carry on", "Write a `<note>` then carry on"),
        ("```\n<note>\nstill in the fence", "```\n<note>\nstill in the fence"),
        # A quoted name earlier in the text does not hide a real half tag later.
        ('Use `<note` to open, then <image src="a', "Use `<note` to open, then"),
    ],
)
def test_a_partial_message_shows_no_marker_and_no_half_tag(so_far, shown):
    assert partial_text(so_far) == shown
