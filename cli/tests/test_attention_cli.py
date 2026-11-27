"""``mael attention`` over a fake Orchestrator client.

What the report holds is ``GET /api/waiting``'s contract, tested in
``orchestrator-api/tests/test_waiting.py``. This tests what the command prints.
"""

import json
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from mael_cli import attention_cli
from mael_cli.orchestrator_client import OrchestratorRefused
from mael_domain.protocol import WaitingAttention, WaitingReport


def item(kind: str, **over) -> WaitingAttention:
    return {
        "id": f"at-{kind}",
        "kind": kind,
        "agentId": "ag1",
        "taskId": "northwind/NORT-7",
        "documentId": None,
        "requestId": "req-1",
        "summary": f"{kind} summary",
        "raisedAt": "2026-09-04T09:00:00Z",
        "clearedAt": None,
        "project": "northwind",
        "taskTitle": "Add order export",
        "documentTitle": "",
        **over,
    }  # type: ignore[typeddict-item]


#: Over the 100 characters a line shows.
LONG = "Should I\nrebase first? " + "The branch is behind main. " * 4

REPORT: WaitingReport = {
    "attention": [
        item("plan_review"),
        item(
            "document_review",
            id="at-doc",
            documentId="doc-1",
            documentTitle="Task set",
        ),
        item(
            "question",
            agentId="ag9",
            taskId=None,
            requestId=None,
            project="acme",
            taskTitle="",
        ),
        item("agent_exited"),
    ],
    "unanswered": [
        {
            "agentId": "ag2",
            "taskId": "northwind/NORT-8",
            "project": "northwind",
            "taskTitle": "Fix the build",
            "lastMessage": LONG,
            "lastMessageAt": "2026-09-04T08:00:00Z",
        }
    ],
    "awaitingMerge": [
        {
            "taskId": "acme/ACME-1",
            "project": "acme",
            "title": "Ship it",
            "prs": [{"number": 12, "url": "https://github.com/acme/acme/pull/12"}],
        },
        {"taskId": "acme/ACME-2", "project": "acme", "title": "No PR", "prs": []},
    ],
}

EMPTY = {"attention": [], "unanswered": [], "awaitingMerge": []}


@pytest.fixture
def server(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(attention_cli.OrchestratorClient, "from_config", lambda: client)
    return client


def test_each_line_names_where_what_waits_and_the_command_that_answers_it(server):
    server.get.return_value = REPORT

    result = CliRunner().invoke(attention_cli.attention, [])

    assert result.exit_code == 0, result.output
    server.get.assert_called_once_with("/api/waiting")
    assert result.output == (
        "Attention:\n"
        "  northwind  NORT-7  plan review: Add order export\n"
        "    mael agent approve ag1 --request req-1  |  "
        "mael agent deny ag1 --request req-1\n"
        "  northwind  NORT-7  document review: Task set\n"
        '    mael doc approve doc-1  |  mael doc request-changes doc-1 --summary "..."\n'
        "  acme  ag9  question: question summary\n"
        "    mael agent answer ag9 CHOICE\n"
        "  northwind  NORT-7  exited: Add order export\n"
        "    mael agent resume ag1\n"
        "\n"
        "Unanswered agents:\n"
        "  northwind  NORT-8  Fix the build\n"
        "    2026-09-04T08:00:00Z  Should I rebase first? The branch is behind main. "
        "The branch is behind main. The branch is behind m…\n"
        '    mael agent say ag2 "..."\n'
        "\n"
        "Awaiting merge:\n"
        "  acme  ACME-1  Ship it\n"
        "    https://github.com/acme/acme/pull/12\n"
        "  acme  ACME-2  No PR\n"
        "    no PR registered\n"
    )


def test_with_nothing_waiting_it_says_so(server):
    server.get.return_value = EMPTY

    result = CliRunner().invoke(attention_cli.attention, [])

    assert result.output == "Nothing waits on you.\n"


def test_json_prints_the_reply_as_the_server_sent_it(server):
    server.get.return_value = REPORT

    result = CliRunner().invoke(attention_cli.attention, ["--json"])

    assert json.loads(result.output) == REPORT


def test_a_refusal_prints_the_server_s_message(server):
    server.get.side_effect = OrchestratorRefused("invalid", "Not ready", 503)

    result = CliRunner().invoke(attention_cli.attention, [])

    assert result.exit_code == 1
    assert "Not ready" in result.output
