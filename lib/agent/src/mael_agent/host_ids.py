"""The host prefix on the id of an agent that runs on a **Remote host**.

A local agent keeps its bare id. An agent on the remote host ``cobalt`` is
``cobalt:<id>`` on the parent server. The prefix is added and removed by the
parent's ``DaemonRouter`` only; the remote host itself never sees it. See
``docs/dev/remote-hosts.md``, "Ids carry the host".
"""

import re

#: Separates the host from the bare id. A host name holds no ``:``.
HOST_SEP = ":"

#: What a host name may be, and what ``hosts:`` in the config enforces. The UI
#: splits subagent ids on ``.``, so a host name holds no ``.`` either.
HOST_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


def split_host(agent_id: str) -> tuple[str, str]:
    """``(host, bare id)``. The host is ``""`` for a local agent.

    Only a prefix that is a valid host name counts, so a local id that holds a
    ``:`` of its own stays whole.
    """
    host, sep, bare = agent_id.partition(HOST_SEP)
    if sep and HOST_NAME.fullmatch(host):
        return host, bare
    return "", agent_id


def with_host(host: str, bare: str) -> str:
    """``bare`` as the parent names it: prefixed when ``host`` is not local."""
    return f"{host}{HOST_SEP}{bare}" if host and bare else bare
