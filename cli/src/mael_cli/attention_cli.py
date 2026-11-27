"""``mael attention``: everything that waits on the user, across every project.

A thin client over ``GET /api/waiting``. It reads this host's own server: the
world there already holds each **Remote host**'s attention items.
"""

import json

import click

from mael_domain.protocol import (
    AwaitingMergeTask,
    UnansweredAgent,
    WaitingAttention,
    WaitingReport,
)

from .json_flag import wants_json
from .orchestrator_client import OrchestratorClient, OrchestratorRefused

#: How much of an agent's last message a line shows.
_EXCERPT = 100

#: The words for each attention kind.
_KIND_WORDS = {
    "question": "question",
    "permission": "permission",
    "plan_review": "plan review",
    "document_review": "document review",
    "agent_exited": "exited",
    "ci_failed": "CI failed",
}


@click.command("attention")
@click.option(
    "--json", "as_json", is_flag=True, help="Print the reply as the server sent it."
)
def attention(as_json: bool) -> None:
    """List what waits on the user, and the command that answers each."""
    try:
        report: WaitingReport = OrchestratorClient.from_config().get("/api/waiting")
    except OrchestratorRefused as exc:
        raise click.ClickException(exc.message) from exc
    if as_json or wants_json():
        click.echo(json.dumps(report, indent=2))
        return
    sections = [
        ("Attention", [_attention_line(item) for item in report["attention"]]),
        ("Unanswered agents", [_unanswered_line(row) for row in report["unanswered"]]),
        ("Awaiting merge", [_merge_line(row) for row in report["awaitingMerge"]]),
    ]
    shown = [(heading, lines) for heading, lines in sections if lines]
    if not shown:
        click.echo("Nothing waits on you.")
        return
    for index, (heading, lines) in enumerate(shown):
        if index:
            click.echo()
        click.echo(f"{heading}:")
        for line in lines:
            click.echo(line)


def _where(project: str, task_id: str | None, agent_id: str | None) -> str:
    """``project  task`` for a task's item, ``project  agent`` for a free agent's."""
    subject = _bare(task_id) if task_id else agent_id or ""
    return f"{project or '-'}  {subject}"


def _bare(task_id: str) -> str:
    """The notebook id of a wire id, ``<project>/<notebook id>``."""
    return task_id.rpartition("/")[2]


def _attention_line(item: WaitingAttention) -> str:
    kind = _KIND_WORDS.get(item["kind"], item["kind"])
    # A plan_review's document is always titled "Plan": use the task title.
    document = item["documentTitle"] if item["kind"] == "document_review" else ""
    what = document or item["taskTitle"] or item["summary"]
    return (
        f"  {_where(item['project'], item['taskId'], item['agentId'])}  {kind}: {what}\n"
        f"    {_answer(item)}"
    )


def _answer(item: WaitingAttention) -> str:
    """The command that answers the item."""
    agent = item["agentId"] or ""
    kind = item["kind"]
    # An agent with several waits open needs to be told which one.
    request = f" --request {item['requestId']}" if item["requestId"] else ""
    if kind == "question":
        return f"mael agent answer {agent} CHOICE{request}"
    if kind in ("plan_review", "permission"):
        return (
            f"mael agent approve {agent}{request}  |  mael agent deny {agent}{request}"
        )
    if kind == "document_review" and item["documentId"]:
        doc = item["documentId"]
        return (
            f'mael doc approve {doc}  |  mael doc request-changes {doc} --summary "..."'
        )
    if kind == "agent_exited":
        return f"mael agent resume {agent}"
    return f"mael agent show {agent}"


def _unanswered_line(row: UnansweredAgent) -> str:
    message = " ".join(row["lastMessage"].split())
    if len(message) > _EXCERPT:
        message = message[: _EXCERPT - 1] + "…"
    return (
        f"  {_where(row['project'], row['taskId'], None)}  {row['taskTitle']}\n"
        f"    {row['lastMessageAt']}  {message}\n"
        f'    mael agent say {row["agentId"]} "..."'
    )


def _merge_line(row: AwaitingMergeTask) -> str:
    pr = row["prs"][-1] if row["prs"] else None
    link = (pr["url"] or f"PR #{pr['number']}") if pr else "no PR registered"
    return (
        f"  {_where(row['project'], row['taskId'], None)}  {row['title']}\n    {link}"
    )
