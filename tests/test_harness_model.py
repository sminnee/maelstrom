"""Public model-reference and harness-transport decisions."""

import asyncio
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from maelstrom.harness_model import (
    HARNESS_CLAUDE,
    HARNESS_CODEX,
    HARNESS_OPENCODE,
    TRANSPORT_CLI,
    TRANSPORT_DAEMON,
    resolve_model_reference,
    resolve_transport,
)
from maelstrom.task_cli import resolve_harness_or_fail
from maelstrom.worktree_launcher import build_harness_command, launch_claude_in_worktree


@pytest.mark.parametrize(
    ("model", "harness", "alias"),
    [
        ("", HARNESS_CLAUDE, "opus"),
        ("sonnet", HARNESS_CLAUDE, "sonnet"),
        ("claude:fable", HARNESS_CLAUDE, "fable"),
        ("codex:luna", HARNESS_CODEX, "luna"),
        ("codex:astra", HARNESS_CODEX, "astra"),
        ("opencode:kimi", HARNESS_OPENCODE, "kimi"),
        ("opencode:glm", HARNESS_OPENCODE, "glm"),
        ("opencode:glm-flash", HARNESS_OPENCODE, "glm-flash"),
        ("opencode:qwen", HARNESS_OPENCODE, "qwen"),
        ("opencode:qwen-flash", HARNESS_OPENCODE, "qwen-flash"),
        ("opencode:deepseek", HARNESS_OPENCODE, "deepseek"),
    ],
)
def test_model_reference_resolves_the_supported_forms(model, harness, alias):
    ref = resolve_model_reference(model)
    assert ref.harness == harness
    assert ref.alias == alias
    assert ref.cli_args == ("--model", alias)


def test_codex_mode_settings_never_request_an_interactive_plan_mode():
    assert resolve_model_reference("codex:terra", "plan").mode_args == (
        "--sandbox",
        "read-only",
    )
    assert resolve_model_reference("codex:terra", "normal").mode_args == (
        "--sandbox",
        "workspace-write",
    )
    assert resolve_model_reference("codex:terra", "auto").mode_args == (
        "--sandbox",
        "workspace-write",
        "--ask-for-approval",
        "on-request",
    )


def test_transport_defaults_to_cli_and_only_accepts_the_two_values():
    with patch.dict(os.environ, {}, clear=True):
        assert resolve_transport() == TRANSPORT_CLI
    with patch.dict(os.environ, {"MAEL_HARNESS_TYPE": "daemon"}, clear=True):
        assert resolve_transport() == TRANSPORT_DAEMON
    with patch.dict(os.environ, {"MAEL_HARNESS_TYPE": "codex"}, clear=True):
        assert resolve_transport() == TRANSPORT_CLI


def test_transport_flags_are_mutually_exclusive():
    assert resolve_transport(cli=True) == TRANSPORT_CLI
    assert resolve_transport(daemon=True) == TRANSPORT_DAEMON
    with pytest.raises(ValueError, match="--cli conflicts with --daemon"):
        resolve_transport(cli=True, daemon=True)


def test_cli_command_uses_the_model_prefix_and_omits_a_mismatched_alias():
    assert build_harness_command(model="codex:terra", permission_mode="plan") == [
        "codex",
        "--sandbox",
        "read-only",
        "--model",
        "terra",
    ]
    assert build_harness_command(model="codex:terra", harness="claude") == ["claude"]


def test_opencode_cli_passes_its_selected_alias():
    assert build_harness_command(model="opencode:kimi") == [
        "opencode",
        "--model",
        "kimi",
    ]


def test_cli_flags_select_transport_and_here_refuses_daemon():
    assert resolve_harness_or_fail(cli=True) == TRANSPORT_CLI
    assert resolve_harness_or_fail(daemon=True) == TRANSPORT_DAEMON
    with pytest.raises(Exception, match="--here cannot use --daemon"):
        resolve_harness_or_fail(daemon=True, here=True)


def test_daemon_refuses_a_codex_model_before_starting_any_process():
    with pytest.raises(ValueError, match="codex daemon is not available"):
        asyncio.run(
            launch_claude_in_worktree(
                Path("/tmp"),
                project="p",
                worktree="alpha",
                model="codex:astra",
                harness=TRANSPORT_DAEMON,
            )
        )
