"""The messages that carry a jig's **Feedback** to the agents of a worktree."""

import pytest

from mael_domain.feedback import (
    format_instruction,
    format_monkeypatch,
    format_param_set,
)


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


def test_an_instruction_with_no_element_is_the_text_alone():
    assert format_instruction("feat/orders", "  Make the header sticky.\n") == (
        "From the jig for feat/orders:\n\nMake the header sticky."
    )


def test_an_instruction_about_an_element_describes_it_first():
    element = {
        "selector": "#toolbar > button._chip_:nth-of-type(2)",
        "tag": "button",
        "classes": ["_chip_x1y2z", "active"],
        "text": "Save",
        "components": ["Chip", "Toolbar"],
        "styles": {"padding": "4px 8px", "font-size": "12px"},
        "ancestors": ["div#toolbar", "main._page_"],
        "url": "http://localhost:61000/?story=chip--default",
        "story": "chip--default",
    }

    assert format_instruction("feat/orders", "Make it bigger.", element) == (
        "From the jig for feat/orders, about this element:\n"
        "    selector: #toolbar > button._chip_:nth-of-type(2)\n"
        "    tag: button\n"
        "    classes: _chip_x1y2z active\n"
        '    text: "Save"\n'
        "    components: Chip < Toolbar\n"
        "    styles: padding: 4px 8px; font-size: 12px\n"
        "    ancestors: div#toolbar < main._page_\n"
        "    story: chip--default\n"
        "    url: http://localhost:61000/?story=chip--default\n"
        "\n"
        "Make it bigger."
    )


def test_an_element_carries_only_the_fields_it_has():
    """A page that is not React has no components, and only Ladle has a story."""
    element = {"selector": "p", "tag": "p", "classes": [], "text": ""}

    assert format_instruction("feat/orders", "Hi", element) == (
        "From the jig for feat/orders, about this element:\n"
        "    selector: p\n"
        "    tag: p\n"
        "\n"
        "Hi"
    )


@pytest.mark.parametrize(
    "field",
    [{"tag": ["p"]}, {"styles": ["x"]}, {"text": 3}, {"url": None}, {"story": {}}],
    ids=["list tag", "list styles", "number text", "null url", "object story"],
)
def test_an_element_field_of_the_wrong_shape_is_left_out(field):
    """Only the selector is validated: the rest is best effort from the browser."""
    element = {"selector": "p", **field}

    assert format_instruction("feat/orders", "Hi", element) == (
        "From the jig for feat/orders, about this element:\n    selector: p\n\nHi"
    )


def test_an_instruction_with_a_screenshot_says_one_is_attached():
    assert format_instruction("feat/orders", "Hi", screenshot=True) == (
        "From the jig for feat/orders:\n\nHi\n\nA screenshot of the viewport is attached."
    )


def test_a_monkeypatch_with_a_screenshot_says_one_is_attached():
    assert format_monkeypatch("feat/orders", ".chip{}", screenshot=True) == (
        "Monkeypatch for feat/orders, from the jig (.drafts/monkeypatch.css):\n"
        "\n"
        "    .chip{}\n"
        "\n"
        "Apply it in source, then delete .drafts/monkeypatch.css.\n"
        "A screenshot of the viewport is attached."
    )


@pytest.mark.parametrize(("value", "word"), [(True, "on"), (False, "off")])
def test_a_param_set_names_the_param_by_its_label_and_the_service(value, word):
    assert (
        format_param_set("feat/orders", "web", "Jig", value)
        == f"The user turned Jig {word} for web on feat/orders."
    )
