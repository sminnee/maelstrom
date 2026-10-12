"""The messages that carry a jig's **Feedback** to the agents of a worktree.

One formatter per Feedback type. The server picks one by the type's name; see
``docs/dev/orchestrator-ui.md``, "The jig".
"""

#: Where the jig keeps the **Monkeypatch**, relative to the worktree root.
MONKEYPATCH_PATH = ".drafts/monkeypatch.css"


def format_monkeypatch(branch: str, css: str, note: str | None = None) -> str:
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
    return "\n".join(lines)


def format_jig_hidden(branch: str) -> str:
    """The message that tells the agents of a worktree the user hid its **Jig**."""
    return f"The user hid the jig for {branch}."
