"""Tests for the ``--json`` flag accessor."""

import click

from mael_cli.json_flag import wants_json


def test_no_click_context_is_false():
    assert wants_json() is False


def test_a_context_with_no_obj_is_false():
    with click.Context(click.Command("x")):
        assert wants_json() is False


def test_a_context_with_the_flag_set_is_true():
    with click.Context(click.Command("x"), obj={"json": True}):
        assert wants_json() is True
