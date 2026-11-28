"""``mael secret`` against a real orchestrator server."""

import pytest
from click.testing import CliRunner
from served_orchestrator import PROJECT

from mael_cli.secret_cli import secret

pytestmark = pytest.mark.binds_socket

PASSPHRASE = "correct horse battery"
CANARY = "canary-123"


def mael_secret(*args: str, stdin: str = ""):
    return CliRunner().invoke(secret, list(args), input=stdin, catch_exceptions=False)


def scopes(listed) -> list[str]:
    """The scope of each API_KEY row of a `list`."""
    rows = (line.split() for line in listed.output.splitlines())
    return [row[1] for row in rows if row and row[0] == "API_KEY"]


def test_the_first_unlock_creates_the_store_and_a_set_reaches_it(start_server):
    start_server()

    created = mael_secret("unlock", stdin=PASSPHRASE + "\n")
    # A piped passphrase loses its final newline: this one has none.
    again = mael_secret("unlock", stdin=PASSPHRASE)
    wrong = mael_secret("unlock", stdin="not the passphrase\n")
    set_ = mael_secret("set", "API_KEY", "-p", PROJECT, stdin=CANARY)
    set_global = mael_secret("set", "API_KEY", "--global", stdin=CANARY + "\n")
    listed = mael_secret("list")
    removed = mael_secret("rm", "API_KEY", "-p", PROJECT)
    after = mael_secret("list")

    assert (created.exit_code, created.output) == (0, "Created the Secret store.\n")
    assert (again.exit_code, again.output) == (0, "Unlocked the Secret store.\n")
    assert wrong.exit_code == 1
    assert set_.output == f"Set API_KEY ({PROJECT}).\n"
    assert set_global.output == "Set API_KEY (global).\n"
    assert listed.exit_code == 0
    assert listed.output.split()[:3] == ["NAME", "SCOPE", "SET"]
    assert scopes(listed) == ["global", PROJECT]
    assert removed.output == f"Removed API_KEY ({PROJECT}).\n"
    assert scopes(after) == ["global"]
    shown = "".join(r.output for r in (set_, set_global, listed, removed, after))
    assert CANARY not in shown


def test_a_set_on_a_store_that_is_not_unlocked_names_the_unlock(start_server):
    start_server()

    result = mael_secret("set", "API_KEY", "--global", stdin=CANARY)
    listed = mael_secret("list")

    assert result.exit_code == 1
    assert "mael secret unlock" in result.output
    assert listed.output == "The Secret store is locked.\nNo secrets yet.\n"


def test_a_command_outside_a_project_asks_for_a_scope(
    start_server, tmp_path, monkeypatch
):
    start_server()
    monkeypatch.chdir(tmp_path)

    result = mael_secret("set", "API_KEY", stdin=CANARY)

    assert result.exit_code == 1
    assert "--global" in result.output
