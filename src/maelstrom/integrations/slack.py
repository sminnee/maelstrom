"""Slack incoming-webhook posting integration for maelstrom."""

import click

from ..context import load_global_config
from ._http import request_text


def resolve_webhook(channel: str | None) -> tuple[str, str]:
    """Resolve a configured Slack webhook to ``(url, name)``.

    Webhooks live under ``slack.webhooks`` in ``~/.maelstrom/config.yaml`` as a
    named map. The dict preserves YAML insertion order, so "first defined" is a
    well-defined default.

    Args:
        channel: Webhook name to select, or None for the first-defined default.

    Returns:
        Tuple of (webhook_url, resolved_name).

    Raises:
        click.ClickException: If no webhooks are configured, or the requested
            channel is not among the configured names.
    """
    webhooks = load_global_config().slack_webhooks

    if not webhooks:
        raise click.ClickException(
            "No Slack webhooks configured. Add them to ~/.maelstrom/config.yaml:\n"
            "  slack:\n"
            "    webhooks:\n"
            "      weekly-update: https://hooks.slack.com/services/XXX\n"
            "      alerts:        https://hooks.slack.com/services/YYY"
        )

    if channel is None:
        name = next(iter(webhooks))
        return (webhooks[name], name)

    if channel not in webhooks:
        available = ", ".join(webhooks)
        raise click.ClickException(
            f"Unknown Slack channel '{channel}'. Configured channels: {available}."
        )

    return (webhooks[channel], channel)


def post_message(webhook_url: str, text: str) -> None:
    """Post a plain-text message to a Slack incoming webhook.

    Slack replies with the literal body ``ok`` (not JSON) on success, so this
    uses :func:`request_text` rather than ``request_json``.

    Raises:
        click.ClickException: On an HTTP error (reused from the HTTP wrapper).
    """
    request_text(webhook_url, method="POST", json_body={"text": text})


@click.group("slack")
def slack():
    """Post messages to Slack via configured webhooks."""
    pass


@slack.command("post")  # type: ignore[attr-defined]
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
    stdin_text = click.get_text_stream("stdin").read().rstrip("\n")
    if message is not None and stdin_text:
        raise click.ClickException(
            "Provide the message as an argument OR via stdin, not both."
        )
    message = (message if message is not None else stdin_text).rstrip("\n")
    if not message:
        raise click.ClickException(
            "No message provided (pass an argument or pipe via stdin)."
        )
    webhook, name = resolve_webhook(channel)
    post_message(webhook, message)
    click.echo(f"Posted to #{name}.")
