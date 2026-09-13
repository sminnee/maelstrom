"""The markers an agent writes in its own message, shared by the two readers.

The daemon and the orchestrator both read the note an agent writes, and they
must agree: a note left in ``last_message`` by one and cut by the other would
show the same words twice. The rule lives here so neither owns it.

Dependencies point one way — CLI → model → store — so this is a leaf. It
imports the standard library and nothing of maelstrom's, which is what lets
both :mod:`maelstrom.agent_model` and
:mod:`maelstrom.orchestrator.document_tags` sit above it.
"""

import re

#: What sits between a tag's name and its closing ``>``: quoted values, and
#: anything that is neither a quote nor a ``>``. A tag ends at the ``>`` that
#: closes it, so a value may hold one.
ATTRIBUTES = r'((?:"[^"]*"|[^>"])*)'

#: What an agent is doing now, in its own words. No attributes are read; the
#: body is the note.
NOTE_TAG = re.compile(rf"<note\b{ATTRIBUTES}>\n?(.*?)\n?</note>", re.DOTALL)


def read_note(text: str) -> tuple[str, str]:
    """``text`` with its note tags cut, and the note they carried.

    The last note wins: a note replaces rather than accumulates, so a message
    holding two is reporting the later one. The note is empty when the message
    carried none, and the text comes back unchanged.

    The span is cut because a note is a field, not prose the reader sees twice.
    """
    note = ""
    for match in NOTE_TAG.finditer(text):
        note = match.group(2)
    if not note:
        return text, ""
    return re.sub(r"\n{3,}", "\n\n", NOTE_TAG.sub("", text)).strip(), note
