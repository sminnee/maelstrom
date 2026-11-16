"""Tests for mael_domain.comm_codename — the **Comm codename** the model proposes."""

import pytest

from mael_domain.comm_codename import generate_codename
from mael_domain.task_metadata_generator import SlugGenerator


class _Replies:
    """A naming model that answers from a list, and counts the calls."""

    def __init__(self, *replies: str | Exception) -> None:
        self._replies = list(replies)
        self.calls = 0

    def complete(self, prompt: str, system: str) -> str:
        self.calls += 1
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.mark.parametrize(
    ("reply", "codename"),
    [
        ("Lantern", "Lantern"),
        ("  night owl.\n", "Night Owl"),
        ("TIDEPOOL", "Tidepool"),
        ("Lantern\nIt evokes a light in the dark.", "Lantern"),
    ],
)
def test_a_good_reply_is_kept_in_title_case(reply, codename):
    assert generate_codename("Invoice export", "", set(), _Replies(reply)) == codename


@pytest.mark.parametrize(
    "bad", ["Three Word Name", "Lantern-7", "", "Sure! Here it is", RuntimeError()]
)
def test_a_bad_reply_gets_one_retry(bad):
    gen = _Replies(bad, "Lantern")
    assert generate_codename("Invoice export", "", set(), gen) == "Lantern"
    assert gen.calls == 2


def test_two_bad_replies_leave_the_codename_blank():
    gen = _Replies("one two three", "4ever")
    assert generate_codename("Invoice export", "", set(), gen) == ""


def test_a_codename_in_use_is_refused_whatever_its_case():
    gen = _Replies("LANTERN", "lantern")
    assert generate_codename("Invoice export", "", {"Lantern"}, gen) == ""
    assert gen.calls == 2


def test_no_naming_model_gives_a_blank_codename():
    assert generate_codename("Invoice export", "", set(), SlugGenerator()) == ""
