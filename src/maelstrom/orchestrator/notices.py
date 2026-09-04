"""Change notices: which entities a batch of events touched.

Pure. A notice names a kind and the ids that changed, and nothing else: the
client refetches what it shows and finds each id present or gone. See
``docs/dev/orchestrator-server.md``, "Change notices".
"""

from .protocol import ENTITY_KINDS, ServerEvent

#: The kinds a notice may name: every entity kind but ``comment``, which
#: folds into ``document`` because the client fetches comments with theirs.
NOTICE_KINDS = tuple(kind for kind in ENTITY_KINDS if kind != "comment")

Notices = dict[str, set[str]]


def notices_for(events: list[ServerEvent]) -> Notices:
    """The notices ``events`` amount to: each ``upsert`` and ``remove`` names its id.

    A transcript event names nothing: transcripts travel on their own stream.
    A comment names its document, because the client fetches comments with
    the document; a comment removal names no document, so it names none.
    """
    out: Notices = {}
    for event in events:
        kind = event.get("type")
        if kind not in ("upsert", "remove"):
            continue
        entity_kind = event["kind"]
        if entity_kind == "comment":
            entity = event.get("entity") or {}
            document_id = entity.get("documentId")
            ids = out.setdefault("document", set())
            if document_id:
                ids.add(document_id)
            continue
        if entity_kind not in NOTICE_KINDS:
            continue
        entity_id = event["entity"]["id"] if kind == "upsert" else event["id"]
        out.setdefault(entity_kind, set()).add(entity_id)
    return out


def merge_notices(into: Notices, more: Notices) -> None:
    """Fold ``more`` into ``into``: a kind with no ids stays a kind with no ids."""
    for kind, ids in more.items():
        into.setdefault(kind, set()).update(ids)
