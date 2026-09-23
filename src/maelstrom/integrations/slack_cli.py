"""The ``mael slack`` commands."""

import sys

import click

from . import slack
from .group_cli import IntegrationGroup


@click.group("slack", cls=IntegrationGroup)
def slack_group():
    """Post messages to Slack via configured webhooks."""
    pass


@slack_group.command("post")  # type: ignore[attr-defined]
@click.argument("message", required=False)
@click.option(
    "--channel",
    default=None,
    help="Webhook name from slack.webhooks (default: first defined).",
)
def cmd_post(message: str | None, channel: str | None) -> None:
    """Post MESSAGE to Slack (reads from stdin when MESSAGE is omitted)."""
    # Read stdin even when an argument is given so we can reject the ambiguous
    # "both" case. isatty() can't tell "piped-but-empty" from "no input" under
    # non-interactive runs (cron/CI/CliRunner), so we key off actual content:
    # stdin only counts as "provided" when it carries a non-blank body.
    #
    # But only read when stdin isn't an interactive terminal — a bare TTY has no
    # pending input, so an unconditional read() blocks forever waiting for the
    # user (e.g. `mael slack post "hi"` from a shell). A TTY can never be the
    # "piped" side of the ambiguity, so skipping its read is always safe.
    stdin_text = "" if sys.stdin.isatty() else sys.stdin.read().rstrip("\n")
    if message is not None and stdin_text:
        raise click.ClickException(
            "Provide the message as an argument OR via stdin, not both."
        )
    message = (message if message is not None else stdin_text).rstrip("\n")
    if not message:
        raise click.ClickException(
            "No message provided (pass an argument or pipe via stdin)."
        )
    webhook, name = slack.resolve_webhook(channel)
    slack.post_message(webhook, message)
    click.echo(f"Posted to #{name}.")
