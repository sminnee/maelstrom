"""The Codex app-server approvals, as the adapter and the normaliser both read them.

Pure. See ``docs/dev/codex-app-server-events.md``, "Approvals".
"""

from dataclasses import dataclass
from typing import Any

#: The server requests that become a Maelstrom permission ask, by the tool they ask for.
APPROVAL_TOOLS = {
    "item/commandExecution/requestApproval": "Bash",
    "item/fileChange/requestApproval": "Edit",
}

#: The key the adapter puts the deny reason under, beside its reply to a
#: request. Codex takes no reason; the transcript shows it.
REASON_KEY = "mael_reason"


@dataclass(frozen=True)
class Approval:
    """One approval request, as the permission ask it is."""

    tool_use_id: str
    tool: str
    input: dict[str, Any]
    description: str
    #: The refusal Codex offers: ``decline``, else ``cancel``, which also
    #: interrupts the turn.
    decline: str
    #: The decision that allows the request and stops Codex asking again, or
    #: ``None`` when Codex offers none. A command-prefix rule comes first, else
    #: the session.
    remember: str | dict[str, Any] | None = None
    #: What :attr:`remember` remembers, for the button that sends it.
    remember_label: str = ""


def approval_of(method: str, params: dict[str, Any]) -> Approval | None:
    """The ask a server request is, or ``None`` for one that is no approval."""
    tool = APPROVAL_TOOLS.get(method)
    if tool is None:
        return None
    if tool == "Bash":
        tool_input: dict[str, Any] = {
            "command": command_of(params),
            "cwd": str(params.get("cwd") or ""),
        }
    else:
        tool_input = {
            key: value
            for key, value in params.items()
            if key not in {"threadId", "turnId", "itemId", "reason", "startedAtMs"}
        }
    available = params.get("availableDecisions")
    declines = not isinstance(available, list) or "decline" in available
    remember, remember_label = _remember_of(available)
    return Approval(
        tool_use_id=str(params.get("itemId") or ""),
        tool=tool,
        input=tool_input,
        description=str(params.get("reason") or f"Codex {tool} request"),
        decline="decline" if declines else "cancel",
        remember=remember,
        remember_label=remember_label,
    )


def _remember_of(available: Any) -> tuple[str | dict[str, Any] | None, str]:
    """The remember decision ``availableDecisions`` offers, and its label."""
    if not isinstance(available, list):
        return None, ""
    for decision in available:
        if isinstance(decision, dict) and "acceptWithExecpolicyAmendment" in decision:
            amendment = decision["acceptWithExecpolicyAmendment"]
            prefix = (
                amendment.get("execpolicy_amendment")
                if isinstance(amendment, dict)
                else None
            )
            if isinstance(prefix, list) and prefix:
                return decision, f"Always allow `{_shown_prefix(prefix)}`"
    if "acceptForSession" in available:
        return "acceptForSession", "Allow for this session"
    return None, ""


#: The shell wrapper Codex runs a command in: ``<shell> -lc <command>``.
_SHELL_FLAGS = {"-c", "-lc"}


def _shown_prefix(prefix: list[Any]) -> str:
    """The rule's prefix as the ask shows its command: without the shell wrapper."""
    words = [str(word) for word in prefix]
    if len(words) == 3 and words[1] in _SHELL_FLAGS:
        return words[2]
    return " ".join(words)


def command_of(params: dict[str, Any]) -> str:
    """The command the agent wrote, not the shell wrapper Codex ran it in."""
    actions = params.get("commandActions")
    if isinstance(actions, list) and len(actions) == 1:
        action = actions[0]
        if isinstance(action, dict) and action.get("command"):
            return str(action["command"])
    return str(params.get("command") or "")


def allowed(decision: Any) -> bool:
    """Whether a reply's decision let the request go ahead."""
    if isinstance(decision, dict):
        return any(str(key).startswith("accept") for key in decision)
    return str(decision).startswith("accept")
