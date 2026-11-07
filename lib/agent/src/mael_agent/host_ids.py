"""The **Id prefix**: the server name at the head of each id a server makes.

A server with ``host_name: cobalt`` names its agents, worktrees and gate jobs
``cobalt:<id>``. A server with no ``host_name`` keeps bare ids. A parent that
merges cobalt's world finds the owner of each id from its prefix, and sends a
request for it to cobalt. See ``docs/dev/remote-hosts.md``, "The id prefix".
"""

import re
from dataclasses import dataclass

#: Separates the owner from the bare id. A host name holds no ``:``.
HOST_SEP = ":"

#: What a host name may be: ``host_name:`` and each ``hosts:`` key. The UI
#: splits subagent ids on ``.``, so a host name holds no ``.``.
HOST_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")

#: The variable a **Delegated agent**'s ``mael`` reads its parent's URL from.
#: The parent's server sets it, and the agent's CLI reads it.
PARENT_URL_ENV = "MAEL_PARENT_URL"


def owner_of(entity_id: str) -> str:
    """The host that owns ``entity_id``, or ``""`` for a bare id.

    Only a prefix that is a valid host name counts, so an id that holds a
    ``:`` of its own, such as a time stamp, has no owner.
    """
    host, sep, _bare = entity_id.partition(HOST_SEP)
    return host if sep and HOST_NAME.fullmatch(host) else ""


@dataclass(frozen=True)
class IdSpace:
    """The ids of one server: each one is ``<prefix>:<bare>``, or bare with no prefix."""

    prefix: str = ""

    def own(self, bare: str) -> str:
        """``bare`` with this space's prefix. The empty prefix changes nothing."""
        return f"{self.prefix}{HOST_SEP}{bare}" if self.prefix and bare else bare

    def strip(self, entity_id: str) -> str:
        """``entity_id`` with this space's prefix removed. Another prefix stays."""
        head = f"{self.prefix}{HOST_SEP}"
        if self.prefix and entity_id.startswith(head):
            return entity_id[len(head) :]
        return entity_id

    def resolve(self, entity_id: str) -> str:
        """``entity_id``, read as one of this space's ids when it has no owner."""
        return entity_id if owner_of(entity_id) else self.own(entity_id)

    def is_own(self, entity_id: str) -> bool:
        """Whether this space made ``entity_id``."""
        return owner_of(entity_id) == self.prefix
