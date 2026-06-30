"""Slack incoming-webhook posting integration for maelstrom."""

import click
from markdown_to_mrkdwn import SlackMarkdownConverter

from ..context import load_global_config
from ._http import request_text

# Slack caps a section block's text object at 3000 characters and a message at
# 50 blocks. We chunk long messages across multiple section blocks to stay under
# the per-block limit (50 * 3000 == 150k chars of headroom before we'd overflow
# the message itself).
SECTION_TEXT_LIMIT = 3000


def _chunk_mrkdwn(text: str, limit: int = SECTION_TEXT_LIMIT) -> list[str]:
    """Split ``text`` into chunks no longer than ``limit`` characters.

    Splits on newline boundaries so formatting spans (which don't cross lines in
    practice) and list items stay intact. A single line longer than ``limit`` is
    hard-split at the limit as a last resort, so the function always terminates.

    Returns at least one chunk (an empty input yields ``[""]``), so callers
    always emit a block.
    """
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        # A lone line longer than the limit can't share a chunk — flush what we
        # have, then hard-split the oversized line into limit-sized pieces.
        if len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(line), limit):
                chunks.append(line[i : i + limit])
            continue

        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate

    if current:
        chunks.append(current)
    # Guarantee at least one chunk so the caller always emits a block, even for
    # empty/blank input (cmd_post already rejects truly empty messages upstream).
    return chunks or [""]


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
    """Post a Markdown message to a Slack incoming webhook.

    Standard Markdown (``**bold**``, ``# headings``, ``[label](url)`` links,
    ``-`` lists, ``> quotes``) is converted to Slack's *mrkdwn* dialect
    (``*bold*``, ``<url|label>``, ``•`` bullets) and sent in Block Kit
    ``section`` blocks, which render formatting over incoming webhooks. The
    newer Block Kit ``markdown`` block renders standard Markdown directly but is
    rejected (HTTP 500) by incoming-webhook URLs, so we convert + section
    instead. The raw, unconverted ``text`` is retained as the top-level fallback
    Slack uses for notifications and non-rendering clients.

    A section block's text caps at 3000 characters, so longer messages are split
    across multiple section blocks (see :func:`_chunk_mrkdwn`); without this
    Slack rejects an oversized block with HTTP 400 ``invalid_blocks``.

    Slack replies with the literal body ``ok`` (not JSON) on success, so this
    uses :func:`request_text` rather than ``request_json``.

    Raises:
        click.ClickException: On an HTTP error (reused from the HTTP wrapper).
    """
    mrkdwn = SlackMarkdownConverter().convert(text)
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": chunk}}
        for chunk in _chunk_mrkdwn(mrkdwn)
    ]
    request_text(
        webhook_url,
        method="POST",
        json_body={"text": text, "blocks": blocks},
    )


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
    #
    # But only read when stdin isn't an interactive terminal — a bare TTY has no
    # pending input, so an unconditional read() blocks forever waiting for the
    # user (e.g. `mael slack post "hi"` from a shell). A TTY can never be the
    # "piped" side of the ambiguity, so skipping its read is always safe.
    stdin = click.get_text_stream("stdin")
    stdin_text = "" if stdin.isatty() else stdin.read().rstrip("\n")
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
