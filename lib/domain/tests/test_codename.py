"""Tests for the codename word list and the draw from it."""

import re

from mael_domain.codename import CODENAMES, pick_codename
from mael_domain.task import _is_yaml_autotyped


class TestTheWordList:
    def test_it_holds_1000_distinct_words(self):
        assert len(CODENAMES) == 1000
        assert len(set(CODENAMES)) == 1000

    def test_each_word_is_3_to_7_lowercase_letters(self):
        assert [w for w in CODENAMES if not re.fullmatch(r"[a-z]{3,7}", w)] == []

    def test_no_word_reads_as_a_non_string_in_yaml(self):
        """A task draft names tasks in YAML, so ``yes`` or ``null`` would mislead."""
        assert [w for w in CODENAMES if _is_yaml_autotyped(w)] == []


class TestPickCodename:
    def test_it_draws_from_the_free_words_alone(self):
        offered: list[str] = []

        def choose(words):
            offered.extend(words)
            return words[0]

        held = {CODENAMES[0], CODENAMES[1]}
        assert pick_codename(held, choose=choose) == CODENAMES[2]
        assert offered == list(CODENAMES[2:])

    def test_it_is_none_when_every_word_is_held(self):
        assert pick_codename(set(CODENAMES)) is None
