"""The marker syntax that the daemon and ``document_tags`` both import.

The spelling has one copy. See docs/dev/orchestrator-server.md.
"""

import re

#: What sits between a tag's name and its closing ``>``: quoted values, and
#: anything that is neither a quote nor a ``>``. A tag ends at the ``>`` that
#: closes it, so a value may hold one — a title reading ``A > B``, or a
#: placeholder an agent copied out of a skill.
ATTRIBUTES = r'((?:"[^"]*"|[^>"])*)'

#: What the agent is doing now. No attributes are read; the body is the note.
NOTE_TAG = re.compile(rf"<note\b{ATTRIBUTES}>\n?(.*?)\n?</note>", re.DOTALL)
