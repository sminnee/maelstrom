"""Slack incoming-webhook posting integration for maelstrom."""

from markdown_to_mrkdwn import SlackMarkdownConverter

from ..context import load_global_config
from ._http import request_text
from .errors import IntegrationError

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
        IntegrationError: If no webhooks are configured, or the requested
            channel is not among the configured names.
    """
    webhooks = load_global_config().slack_webhooks

    if not webhooks:
        raise IntegrationError(
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
        raise IntegrationError(
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
        IntegrationError: On an HTTP error (reused from the HTTP wrapper).
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
