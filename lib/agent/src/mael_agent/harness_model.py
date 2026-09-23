"""Model references and the transport that starts their harness."""

import os
import re
from dataclasses import dataclass

HARNESS_CLAUDE = "claude"
HARNESS_CODEX = "codex"
HARNESS_OPENCODE = "opencode"

TRANSPORT_CLI = "cli"
TRANSPORT_DAEMON = "daemon"
TRANSPORTS = (TRANSPORT_CLI, TRANSPORT_DAEMON)
HARNESS_TYPE_ENV = "MAEL_HARNESS_TYPE"

DEFAULT_MODEL = "claude:opus"

_MODELS = {
    HARNESS_CLAUDE: ("sonnet", "opus", "fable"),
    HARNESS_CODEX: ("luna", "terra", "sol", "astra"),
    HARNESS_OPENCODE: (
        "kimi",
        "glm",
        "glm-flash",
        "qwen",
        "qwen-flash",
        "deepseek",
    ),
}

_CODEX_MODELS = {
    "astra": ("gpt-6-astra", None),
    "sol": ("gpt-5.6-sol", "low"),
    "terra": ("gpt-5.6-terra", "medium"),
    "luna": ("gpt-5.6-luna", None),
}


@dataclass(frozen=True)
class ModelReference:
    """One model choice resolved for a harness command."""

    harness: str
    alias: str
    cli_args: tuple[str, ...]
    mode_args: tuple[str, ...]


def resolve_model_reference(model: str | None, mode: str = "normal") -> ModelReference:
    """Resolve ``model`` to its harness, CLI alias, and harness mode flags.

    Blank and bare values are Claude-compatible. A qualified value chooses the
    matching harness; unknown qualified aliases pass through so each CLI keeps
    authority over its own model catalogue.
    """
    value = model or DEFAULT_MODEL
    prefix, separator, alias = value.partition(":")
    if not separator:
        prefix, alias = HARNESS_CLAUDE, value
    if prefix not in _MODELS:
        raise ValueError(
            f"Unknown model harness: {prefix!r} (expected claude, codex, or opencode)."
        )
    if not alias:
        raise ValueError("A model reference needs an alias after ':'.")
    cli_args = ("--model", alias)
    if prefix == HARNESS_CODEX and (codex_model := _CODEX_MODELS.get(alias)):
        model_id, effort = codex_model
        cli_args = ("--model", model_id)
        if effort:
            cli_args += ("-c", f"model_reasoning_effort={effort}")

    return ModelReference(
        harness=prefix,
        alias=alias,
        cli_args=cli_args,
        mode_args=_mode_args(prefix, mode),
    )


#: What an execute-model alias may contain. The alias reaches the child as the
#: text of a ``/model`` user turn rather than as an argv element, so a newline
#: in it would deliver a second turn of its own. Same character set and same
#: reason as :func:`maelstrom.task.is_safe_id`.
_SAFE_ALIAS = re.compile(r"[A-Za-z0-9._-]+")


def resolve_execute_model(model: str) -> ModelReference:
    """Resolve an execute model, or raise ``ValueError`` saying why it cannot be one.

    The one place the execute-model rules live, so every entry point refuses
    the same value with the same words. Two rules beyond an ordinary reference:

    - it must name the ``claude`` harness, because the switch is a ``/model``
      command and that cannot change which binary is running;
    - its alias must be plain, because it travels as message content.
    """
    ref = resolve_model_reference(model)
    if ref.harness != HARNESS_CLAUDE:
        raise ValueError(
            f"An execute model must be a Claude model, not {ref.harness}: "
            "/model cannot change harness."
        )
    if not _SAFE_ALIAS.fullmatch(ref.alias):
        raise ValueError(f"Unsafe execute model alias: {ref.alias!r}")
    return ref


def _mode_args(harness: str, mode: str) -> tuple[str, ...]:
    if harness == HARNESS_CLAUDE:
        return () if mode == "normal" else ("--permission-mode", mode)
    if harness == HARNESS_OPENCODE:
        return ("--auto",) if mode == "auto" else ()
    options = codex_thread_start_options(mode)
    args = ("--sandbox", str(options["sandbox"]))
    if approval_policy := options.get("approvalPolicy"):
        args += ("--ask-for-approval", str(approval_policy))
    return args


def codex_thread_start_options(mode: str) -> dict[str, str]:
    """Map Maelstrom's Agent mode onto Codex thread-start settings."""
    if mode == "plan":
        return {"sandbox": "read-only"}
    if mode == "auto":
        return {"sandbox": "workspace-write", "approvalPolicy": "on-request"}
    return {"sandbox": "workspace-write"}


def resolve_transport(*, cli: bool = False, daemon: bool = False) -> str:
    """Resolve mutually-exclusive transport flags and inherited transport."""
    if cli and daemon:
        raise ValueError("--cli conflicts with --daemon")
    if cli:
        return TRANSPORT_CLI
    if daemon:
        return TRANSPORT_DAEMON
    inherited = os.environ.get(HARNESS_TYPE_ENV)
    return inherited if inherited in TRANSPORTS else TRANSPORT_CLI
