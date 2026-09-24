"""The harness command and transport the launcher builds from a model reference."""

import asyncio
from pathlib import Path

import pytest

from mael_agent.harness_model import TRANSPORT_CLI, TRANSPORT_DAEMON
from mael_cli.task_cli import resolve_harness_or_fail
from mael_cli.worktree_launcher import build_harness_command, launch_claude_in_worktree


def test_cli_command_uses_the_model_prefix_and_omits_a_mismatched_alias():
    assert build_harness_command(model="codex:terra", permission_mode="plan") == [
        "codex",
        "--sandbox",
        "read-only",
        "--model",
        "gpt-5.6-terra",
        "-c",
        "model_reasoning_effort=medium",
    ]
    assert build_harness_command(model="codex:terra", harness="claude") == ["claude"]


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (
            "codex:astra",
            ["codex", "--sandbox", "workspace-write", "--model", "gpt-6-astra"],
        ),
        (
            "codex:sol",
            [
                "codex",
                "--sandbox",
                "workspace-write",
                "--model",
                "gpt-5.6-sol",
                "-c",
                "model_reasoning_effort=low",
            ],
        ),
        (
            "codex:terra",
            [
                "codex",
                "--sandbox",
                "workspace-write",
                "--model",
                "gpt-5.6-terra",
                "-c",
                "model_reasoning_effort=medium",
            ],
        ),
        (
            "codex:luna",
            ["codex", "--sandbox", "workspace-write", "--model", "gpt-5.6-luna"],
        ),
        (
            "codex:custom",
            ["codex", "--sandbox", "workspace-write", "--model", "custom"],
        ),
    ],
)
def test_codex_commands_resolve_known_aliases_and_set_effort_defaults(model, expected):
    assert build_harness_command(model=model) == expected


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
