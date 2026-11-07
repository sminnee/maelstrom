"""The **Id prefix**: own, strip, owner and resolve."""

import pytest

from mael_agent.host_ids import IdSpace, owner_of

COBALT = IdSpace("cobalt")
BARE = IdSpace()


@pytest.mark.parametrize(
    ("entity_id", "expected"),
    [
        ("cobalt:r1", "cobalt"),
        ("zinc-2:r1.sub", "zinc-2"),
        ("r1", ""),
        # An id with a `:` of its own has no owner.
        ("2026-01-01T00:00:00Z", ""),
        ("Cobalt:r1", ""),
        ("co.balt:r1", ""),
        (":r1", ""),
    ],
)
def test_owner(entity_id, expected):
    assert owner_of(entity_id) == expected


def test_own_and_strip_are_inverse():
    assert COBALT.own("r1") == "cobalt:r1"
    assert COBALT.strip("cobalt:r1") == "r1"
    # Another host's prefix is not this space's to remove.
    assert COBALT.strip("zinc:r1") == "zinc:r1"
    assert COBALT.own("") == ""


def test_the_empty_prefix_changes_nothing():
    assert BARE.own("r1") == "r1"
    assert BARE.strip("r1") == "r1"
    assert BARE.resolve("r1") == "r1"
    assert BARE.is_own("r1")
    assert not BARE.is_own("cobalt:r1")


def test_resolve_reads_a_bare_id_as_own():
    assert COBALT.resolve("maelstrom-alpha") == "cobalt:maelstrom-alpha"
    assert COBALT.resolve("cobalt:maelstrom-alpha") == "cobalt:maelstrom-alpha"
    assert COBALT.resolve("zinc:maelstrom-alpha") == "zinc:maelstrom-alpha"
