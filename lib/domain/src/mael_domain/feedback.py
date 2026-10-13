"""The messages that carry a jig's **Feedback** to the agents of a worktree.

One formatter per Feedback type. The server picks one by the type's name; see
``docs/dev/orchestrator-ui.md``, "The jig".
"""

from typing import Any

#: Where the jig keeps the **Monkeypatch**, relative to the worktree root.
MONKEYPATCH_PATH = ".drafts/monkeypatch.css"

#: Where the agent writes the **Control set** the jig draws, relative to the worktree root.
CONTROLS_PATH = ".drafts/jig-controls.json"

#: Where the jig keeps the value of each **Control**, relative to the worktree root.
VALUES_PATH = ".drafts/jig-values.json"

#: Where the jig writes each screenshot it sends, relative to the worktree root.
JIG_DRAFTS = ".drafts/jig"

#: The line a message ends with when the jig sent a screenshot with it.
SCREENSHOT_LINE = "A screenshot of the viewport is attached."

#: What a **Pick** asks controls for, by its scope.
SCOPES = {
    "element": "this element",
    "similar": "elements like this",
    "area": "this area",
}

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


def _quote(css: str) -> list[str]:
    """CSS indented as one block, so a rule that holds a blank line stays one quote."""
    return [f"    {line}" if line else "" for line in css.strip().splitlines()]


def _note(note: str | None) -> list[str]:
    return [f"Note: {note.strip()}"] if note and note.strip() else []


def format_monkeypatch(
    css: str, note: str | None = None, *, screenshot: bool = False
) -> str:
    """The body of the one event that carries a **Monkeypatch** to the agents of a worktree."""
    lines = [
        f"Monkeypatch from {MONKEYPATCH_PATH}:",
        "",
        *_quote(css),
        "",
        *_note(note),
    ]
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


def format_pick(
    branch: str,
    scope: str,
    element: dict[str, Any],
    note: str | None = None,
    *,
    screenshot: bool = False,
) -> str:
    """The message that asks the agents for a **Control set** for the **Pick**."""
    lines = [
        f"The user picked an element in the jig for {branch} and asks for controls "
        f"for {SCOPES[scope]}:",
        *(f"    {line}" for line in _describe(element)),
        "",
        *_note(note),
        f"Write a control set to {CONTROLS_PATH}, and wire each variable in "
        f"{MONKEYPATCH_PATH}.",
    ]
    if screenshot:
        lines.append(SCREENSHOT_LINE)
    return "\n".join(lines)


def format_controls(
    branch: str,
    title: str,
    changes: list[dict[str, str]],
    css: str | None = None,
    note: str | None = None,
    *,
    screenshot: bool = False,
) -> str:
    """The message that carries the values the user set on each **Control**.

    ``css`` is the **Monkeypatch**, quoted so the agent sees how each variable
    is wired.
    """
    lines = [f"Jig controls for {branch} ({title}):", ""]
    lines += [_change(change) for change in changes]
    lines.append("")
    if css and css.strip():
        lines += [f"Wired in {MONKEYPATCH_PATH}:", "", *_quote(css), ""]
    lines += [
        *_note(note),
        "Apply each value in source, and decide for each one if it is a token or a "
        f"plain number. Then delete {CONTROLS_PATH}, {VALUES_PATH} and the wiring "
        f"in {MONKEYPATCH_PATH}.",
    ]
    if screenshot:
        lines.append(SCREENSHOT_LINE)
    return "\n".join(lines)


def _change(change: dict[str, str]) -> str:
    """One ``--var: from → to;`` line, with the label when it says more than the var."""
    var, label, start = change["var"], change.get("label"), change.get("from")
    value = f"{start} → {change['to']}" if start else change["to"]
    notes = [label] if label and label != var else []
    if not start:
        notes.append("was unset")
    suffix = f"  ({', '.join(notes)})" if notes else ""
    return f"    {var}: {value};{suffix}"


def format_param_set(branch: str, service: str, label: str, value: bool) -> str:
    """The message that tells the agents of a worktree the user set a **Live feedback param**."""
    return (
        f"The user turned {label} {'on' if value else 'off'} for {service} on {branch}."
    )


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
