"""The media a document body names: which refs are files in the worktree."""

import pytest

from mael_domain.document_tags import (
    CompactRequest,
    ParamSet,
    media_refs,
    partial_text,
    read_tags,
    replace_media,
)


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
        ('Pushed.\n\n<link rel="gh-pr">118</link>', "Pushed."),
        ('More work.\n\n<link rel="gh-pr" withdrawn>118</link>', "More work."),
        ('Look.\n\n<live-feedback jig="on">', "Look."),
        ('Built.\n\n<compact-session min-tokens="200k">', "Built."),
        ("Done.\n\n<end-session>", "Done."),
        ('See <image src="docs/shot.png" alt="A"> here', "See  here"),
        # A marker's body is not prose, so nothing shows until it closes.
        ("Before\n\n<note>rebasing on", "Before"),
        ("Before\n\n<milestone>bui", "Before"),
        ('Before\n\n<link rel="gh-pr">https://github.com/o/r/pu', "Before"),
        ('Before\n\n<link rel="gh-pr" withdrawn>11', "Before"),
        # An HTML link never closes, so it is prose, not a held-back marker.
        (
            'a <link rel="stylesheet" href="x.css"> b',
            'a <link rel="stylesheet" href="x.css"> b',
        ),
        ("Before\n\n<lin", "Before"),
        # A half-written tag is held back, whatever it will turn out to be.
        ("Before\n\n<", "Before"),
        ("Before\n\n<doc-", "Before"),
        ('Before\n\n<doc-file kind="pr" filename=".drafts', "Before"),
        ("Before\n\n<user-attention lo", "Before"),
        ("Before\n\n<live-feedback ji", "Before"),
        ("Before\n\n<compact-session min-tok", "Before"),
        ("Before\n\n<end-sess", "Before"),
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


# --- the live feedback marker: the agent sets a param -----------------------


def feedback_params(text: str) -> tuple[ParamSet, ...]:
    return read_tags(text, show_image=lambda _image: None).feedback_params


@pytest.mark.parametrize(
    ("text", "sets"),
    [
        ('<live-feedback jig="on">', [ParamSet(None, "jig", True)]),
        ('<live-feedback jig="off">', [ParamSet(None, "jig", False)]),
        (
            '<live-feedback service="web" jig="on">',
            [ParamSet("web", "jig", True)],
        ),
        (
            '<live-feedback jig="on" grid="off">',
            [ParamSet(None, "jig", True), ParamSet(None, "grid", False)],
        ),
        (
            '<live-feedback jig="on">\nLater.\n<live-feedback service="ladle" jig="off">',
            [ParamSet(None, "jig", True), ParamSet("ladle", "jig", False)],
        ),
    ],
    ids=["on", "off", "one-service", "several-params", "in-order"],
)
def test_a_live_feedback_marker_sets_each_param_it_names(text, sets):
    assert list(feedback_params(text)) == sets


@pytest.mark.parametrize(
    "text",
    [
        '<live-feedback jig="maybe"> Just talking.',
        '<live-feedback jig="on" grid="maybe"> Just talking.',
        '<live-feedback service="web"> Just talking.',
        '<live-feedback service="" jig="on"> Just talking.',
        '<live-feedback dark-mode="on"> Just talking.',
        "<live-feedback> Just talking.",
        'Write `<live-feedback jig="on">` to show it.',
    ],
    ids=[
        "bad-value",
        "one-bad-value",
        "no-param",
        "empty-service",
        "not-a-word",
        "bare",
        "quoted-in-code",
    ],
)
def test_a_live_feedback_marker_with_no_good_param_stays_as_text(text):
    tagged = read_tags(text, show_image=lambda _image: None)
    assert tagged.feedback_params == ()
    assert tagged.text == text


# --- the compaction request: the agent asks for its session to compact ------


def compact(text: str) -> CompactRequest | None:
    return read_tags(text, show_image=lambda _image: None).compact


@pytest.mark.parametrize(
    ("text", "asked"),
    [
        ('<compact-session min-tokens="200k">', CompactRequest(200_000)),
        ('<compact-session min-tokens="1.5M">', CompactRequest(1_500_000)),
        ('<compact-session min-tokens="200000">', CompactRequest(200_000)),
        ("<compact-session>", CompactRequest(None)),
        (
            '<compact-session min-tokens="lots">',
            CompactRequest(None, invalid="lots"),
        ),
        (
            '<compact-session min-tokens="200k">Keep the PR number</compact-session>',
            CompactRequest(200_000, instructions="Keep the PR number"),
        ),
        (
            '<compact-session min-tokens="1m">\n<compact-session min-tokens="200k">',
            CompactRequest(200_000),
        ),
    ],
    ids=["k", "m", "plain", "absent", "invalid", "body", "last-wins"],
)
def test_a_compaction_request_reads_its_threshold_and_focus(text, asked):
    assert compact(text) == asked


def test_a_compaction_request_is_cut_from_the_text():
    tagged = read_tags(
        'Gates pass.\n\n<compact-session min-tokens="200k">Keep it</compact-session>',
        show_image=lambda _image: None,
    )
    assert tagged.text == "Gates pass."


@pytest.mark.parametrize(
    "text",
    [
        'Write `<compact-session min-tokens="200k">` to ask.',
        '```\n<compact-session min-tokens="200k">\n```',
    ],
    ids=["inline", "fenced"],
)
def test_a_compaction_request_quoted_in_code_is_text(text):
    tagged = read_tags(text, show_image=lambda _image: None)
    assert tagged.compact is None
    assert tagged.text == text


def test_a_message_with_no_compaction_request_reports_none():
    assert compact("Just talking.") is None


@pytest.mark.parametrize(
    "text",
    ["Done.\n\n<end-session>", "Done.\n\n<end-session/>", "Done.\n\n<end-session />"],
    ids=["bare", "self-closing", "spaced"],
)
def test_an_end_session_request_is_read_and_cut_from_the_text(text):
    tagged = read_tags(text, show_image=lambda _image: None)
    assert tagged.end_session is True
    assert tagged.text == "Done."


@pytest.mark.parametrize(
    "text",
    ["Write `<end-session>` to end.", "```\n<end-session>\n```"],
    ids=["inline", "fenced"],
)
def test_an_end_session_request_quoted_in_code_is_text(text):
    tagged = read_tags(text, show_image=lambda _image: None)
    assert tagged.end_session is False
    assert tagged.text == text
