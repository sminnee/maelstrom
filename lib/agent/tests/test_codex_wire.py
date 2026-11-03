"""The remember option a Codex approval offers, read off its ``availableDecisions``."""

import pytest

from mael_agent.codex_wire import approval_of

COMMAND = "item/commandExecution/requestApproval"
RULE = {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": ["npm", "test"]}}


@pytest.mark.parametrize(
    ("available", "remember", "label"),
    [
        (
            ["accept", RULE, "acceptForSession", "cancel"],
            RULE,
            "Always allow `npm test`",
        ),
        (
            ["accept", "acceptForSession", "decline"],
            "acceptForSession",
            "Allow for this session",
        ),
        (
            [
                "accept",
                {
                    "acceptWithExecpolicyAmendment": {
                        "execpolicy_amendment": ["/bin/zsh", "-lc", "echo $X"]
                    }
                },
            ],
            {
                "acceptWithExecpolicyAmendment": {
                    "execpolicy_amendment": ["/bin/zsh", "-lc", "echo $X"]
                }
            },
            "Always allow `echo $X`",
        ),
        (
            ["accept", {"acceptWithExecpolicyAmendment": {}}, "acceptForSession"],
            "acceptForSession",
            "Allow for this session",
        ),
        (["accept", "decline"], None, ""),
        (None, None, ""),
    ],
    ids=["rule", "session", "wrapped", "empty-rule", "neither", "no-list"],
)
def test_approval_offers_the_rule_first_else_the_session(available, remember, label):
    params = {"itemId": "exec-1", "command": "npm test", "cwd": "/w"}
    if available is not None:
        params["availableDecisions"] = available
    approval = approval_of(COMMAND, params)
    assert approval is not None
    assert approval.remember == remember
    assert approval.remember_label == label
