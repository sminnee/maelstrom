"""Model references and the transport that starts their harness."""

import os
import re
from dataclasses import dataclass
from typing import Any

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

#: Claude Code aliases that maelstrom resolves to a full id, because Claude Code's
#: own alias still points at an older model.
_CLAUDE_MODELS = {"fable": "claude-fable-5-2"}

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
    #: The model the CLI receives: a full id from the harness's table, else the alias.
    model_id: str
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
    model_id, effort = alias, None
    if prefix == HARNESS_CLAUDE:
        model_id = _CLAUDE_MODELS.get(alias, alias)
    elif prefix == HARNESS_CODEX and (codex_model := _CODEX_MODELS.get(alias)):
        model_id, effort = codex_model
    cli_args = ("--model", model_id)
    if effort:
        cli_args += ("-c", f"model_reasoning_effort={effort}")

    return ModelReference(
        harness=prefix,
        alias=alias,
        model_id=model_id,
        cli_args=cli_args,
        mode_args=_mode_args(prefix, mode),
    )


#: What an execute-model alias may contain. The alias reaches the child as the
#: text of a ``/model`` user turn rather than as an argv element, so a newline
#: in it would deliver a second turn of its own. Same character set and same
#: reason as :func:`mael_domain.task.is_safe_id`.
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
    return args + ("-c", f"approvals_reviewer={options['approvalsReviewer']}")


#: The core plugin, as both harnesses name it from the ``maelstrom`` marketplace.
CORE_PLUGIN = "maelstrom@maelstrom"
#: The optional plugin of development practices, from the same marketplace.
PRACTICES_PLUGIN = "minnstack@maelstrom"

#: The Codex config that enables the core plugin for one thread. Setup installs
#: it disabled, because Codex has no ``--plugin-dir``. It must be a nested
#: table: a dotted key with the quoted ``"maelstrom@maelstrom"`` segment is
#: accepted and ignored.
CODEX_CORE_PLUGIN_CONFIG = {"plugins": {CORE_PLUGIN: {"enabled": True}}}

#: :data:`CODEX_CORE_PLUGIN_CONFIG` as a ``codex -c`` value. An inline table
#: merges into the user's ``plugins``; it does not replace it.
CODEX_CORE_PLUGIN_ARG = f'plugins={{"{CORE_PLUGIN}"={{enabled=true}}}}'


def codex_thread_start_options(mode: str) -> dict[str, str]:
    """Map Maelstrom's Agent mode onto Codex thread-start settings.

    Every mode names its reviewer, so a user's Codex config cannot move it.
    """
    reviewer = {"approvalsReviewer": _CODEX_REVIEWER.get(mode, "user")}
    if mode == "plan":
        return {"sandbox": "read-only", **reviewer}
    if mode == "auto":
        return {
            "sandbox": "workspace-write",
            "approvalPolicy": "on-request",
            **reviewer,
        }
    return {"sandbox": "workspace-write", **reviewer}


#: The approval policy a Codex turn runs under, by mode. ``normal`` asks before
#: each untrusted command, as a Claude ``normal`` session asks.
_CODEX_TURN_APPROVAL = {
    "plan": "on-request",
    "normal": "untrusted",
    "auto": "on-request",
}

#: Who answers a Codex approval ask, by mode; ``user`` for a mode not named.
_CODEX_REVIEWER = {"auto": "auto_review"}

#: A Codex thread-start sandbox mode, as the turn-start sandbox policy.
_CODEX_SANDBOX_POLICY = {
    "read-only": {"type": "readOnly"},
    "workspace-write": {"type": "workspaceWrite"},
}


def codex_turn_policy(mode: str) -> dict[str, Any]:
    """Map Maelstrom's Agent mode onto Codex turn-start settings.

    Sent on every turn. See ``docs/dev/codex-app-server-events.md``, "Modes".
    """
    options = codex_thread_start_options(mode)
    return {
        "sandboxPolicy": _CODEX_SANDBOX_POLICY[options["sandbox"]],
        "approvalPolicy": _CODEX_TURN_APPROVAL.get(mode, "on-request"),
        "approvalsReviewer": options["approvalsReviewer"],
    }


def codex_mode_of(settings: dict[str, Any]) -> str:
    """The Agent mode a Codex turn policy runs, else ``""``.

    The inverse of :func:`codex_turn_policy`: no two modes share a policy.
    """
    sandbox = settings.get("sandboxPolicy")
    sandbox_type = sandbox.get("type") if isinstance(sandbox, dict) else None
    for mode in _CODEX_TURN_APPROVAL:
        policy = codex_turn_policy(mode)
        if (
            sandbox_type == policy["sandboxPolicy"]["type"]
            and settings.get("approvalPolicy") == policy["approvalPolicy"]
        ):
            return mode
    return ""


def is_driven_agent() -> bool:
    """Whether this process runs under the agent daemon, or descends from one."""
    return os.environ.get(HARNESS_TYPE_ENV) == TRANSPORT_DAEMON


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
