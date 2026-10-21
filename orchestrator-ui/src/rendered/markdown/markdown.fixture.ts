/**
 * Two versions of one markdown file, for the stories and the fake `changes`
 * scenario: an edited paragraph, an added list item, a new section and a
 * removed table.
 */
export const OLD_MARKDOWN = `# Token rotation

A token past its expiry rotates, so a client keeps its session.

## Expiry

A token expires after an hour. The client never sees the expiry: the API
swaps the token in its reply.

- Rotation is per client.
- A revoked token never rotates.

## Limits

| Limit | Value |
| --- | --- |
| Rotations per hour | 60 |
| Grace period | 1 minute |

## Testing

\`\`\`sh
uv run pytest -k rotation

uv run pytest -k expiry
\`\`\`
`;

export const NEW_MARKDOWN = OLD_MARKDOWN.replace('after an hour', 'after two hours')
  .replace(
    '- A revoked token never rotates.',
    '- A revoked token never rotates.\n- A rotated token keeps its scopes.',
  )
  .replace(
    /## Limits\n\n[\s\S]*?\n\n## Testing/,
    '## Grace\n\nThe old token stays valid for a minute, so a reply in flight still lands.\n\n## Testing',
  );
