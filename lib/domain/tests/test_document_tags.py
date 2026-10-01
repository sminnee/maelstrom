"""The media a document body names: which refs are files in the worktree."""

from mael_domain.document_tags import media_refs, replace_media


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
