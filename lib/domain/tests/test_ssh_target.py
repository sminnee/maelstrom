"""Tests for the **SSH target** parse and the command it makes."""

import subprocess

import pytest

from mael_domain.ssh_target import SshTarget


def test_a_target_parses_into_its_parts():
    target = SshTarget.parse("sam@monolith.tail.ts.net:/home/sam/p/alpha")
    assert target == SshTarget("sam", "monolith.tail.ts.net", "/home/sam/p/alpha")
    assert str(target) == "sam@monolith.tail.ts.net:/home/sam/p/alpha"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "monolith:/p",  # no user
        "sam@monolith",  # no path
        "sam@monolith:p/alpha",  # a relative path
        "-oProxyCommand=x@monolith:/p",  # a user that reads as an ssh option
        "sam;rm@monolith:/p",  # shell text in the user
        "sam@-monolith:/p",  # a host that reads as an option
        "sam@mono$(id):/p",  # shell text in the host
        "sam@mono lith:/p",
        "sam@monolith:/p\nrm -rf /",  # a newline ends the remote command
    ],
)
def test_parse_refuses_what_is_not_user_at_host_colon_path(text):
    assert SshTarget.parse(text) is None


def test_the_command_opens_a_login_shell_in_the_path():
    target = SshTarget("sam", "monolith", "/home/sam/p/alpha")
    assert target.command() == (
        "ssh -t sam@monolith 'cd /home/sam/p/alpha && exec \"$SHELL\" -l'"
    )


@pytest.mark.parametrize("name", ["a b", "it's $HOME `x`", 'q"uote', "x && y"])
def test_a_hard_path_survives_both_shells(tmp_path, name):
    """The local shell gives ssh its words; the remote shell runs the ``cd``.

    ``ssh`` is a shell function that runs its command in a second shell, as
    sshd does. ``$SHELL`` is a stub that prints where it started.
    """
    path = tmp_path / name
    path.mkdir()
    stub = tmp_path / "login-shell"
    stub.write_text("#!/bin/sh\npwd\n")
    stub.chmod(0o755)
    command = SshTarget("sam", "monolith", str(path)).command()
    out = subprocess.run(
        ["sh", "-c", f'ssh() {{ sh -c "$3"; }}; {command}'],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "SHELL": str(stub)},
    )
    assert out.stdout.strip() == str(path)
