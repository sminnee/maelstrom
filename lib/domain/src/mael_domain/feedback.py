"""The messages that carry a jig's **Feedback** to the agents of a worktree.

One formatter per Feedback type. The server picks one by the type's name; see
``docs/dev/orchestrator-ui.md``, "The jig".
"""

from typing import Any

#: Where the jig keeps the **Monkeypatch**, relative to the worktree root.
MONKEYPATCH_PATH = ".drafts/monkeypatch.css"

#: Where the jig writes each screenshot it sends, relative to the worktree root.
JIG_DRAFTS = ".drafts/jig"

#: The line a message ends with when the jig sent a screenshot with it.
SCREENSHOT_LINE = "A screenshot of the viewport is attached."

#: The fields of a picked element the message names, in the order it names them.
_ELEMENT_FIELDS = (
    "selector",
    "tag",
    "classes",
    "text",
    "components",
    "styles",
    "ancestors",
    "story",
    "url",
)

#: How a list field is joined: the components and ancestors read outward.
_JOINS = {"classes": " ", "components": " < ", "ancestors": " < "}


def format_monkeypatch(
    branch: str, css: str, note: str | None = None, *, screenshot: bool = False
) -> str:
    """The one message that carries a **Monkeypatch** to the agents of a worktree.

    The CSS is indented as a block, so a rule that holds a blank line stays
    one quote.
    """
    quoted = "\n".join(
        f"    {line}" if line else "" for line in css.strip().splitlines()
    )
    lines = [
        f"Monkeypatch for {branch}, from the jig ({MONKEYPATCH_PATH}):",
        "",
        quoted,
        "",
    ]
    if note and note.strip():
        lines.append(f"Note: {note.strip()}")
    lines.append(f"Apply it in source, then delete {MONKEYPATCH_PATH}.")
    if screenshot:
        lines.append(SCREENSHOT_LINE)
    return "\n".join(lines)


def format_instruction(
    branch: str,
    text: str,
    element: dict[str, Any] | None = None,
    *,
    screenshot: bool = False,
) -> str:
    """The message that carries an **Instruction**, about the **Pick** if there is one."""
    if element is None:
        lines = [f"From the jig for {branch}:"]
    else:
        lines = [
            f"From the jig for {branch}, about this element:",
            *(f"    {line}" for line in _describe(element)),
        ]
    lines += ["", text.strip()]
    if screenshot:
        lines += ["", SCREENSHOT_LINE]
    return "\n".join(lines)


def format_jig_hidden(branch: str) -> str:
    """The message that tells the agents of a worktree the user hid its **Jig**."""
    return f"The user hid the jig for {branch}."


def _describe(element: dict[str, Any]) -> list[str]:
    """One ``name: value`` line per field the element has.

    Only the selector is validated, so a field of the wrong shape is left out
    rather than refused.
    """
    lines = []
    for name in _ELEMENT_FIELDS:
        value = element.get(name)
        if isinstance(value, list) and name in _JOINS:
            value = _JOINS[name].join(str(item) for item in value)
        elif isinstance(value, dict) and name == "styles":
            value = "; ".join(f"{key}: {style}" for key, style in value.items())
        elif not isinstance(value, str):
            continue
        if name == "text" and value:
            value = f'"{value}"'
        if value:
            lines.append(f"{name}: {value}")
    return lines
