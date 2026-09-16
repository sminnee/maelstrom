## Overview

Replace quiet fences with user-attention tags. The renderer ranks standalone tags and leaves tags
inside code fences literal. Node summaries remove the tags while transcript markdown keeps them.

## Decisions

- Parse tags in the shared Markdown component because agent prose reaches every Markdown call site.
- Track fenced listings before splitting tags, so code remains literal.
- Remove tags only from `lastMessage`, because transcript and plan prose need the renderer marker.

## Test seams

- `Markdown` renders high and low segments, markdown, unknown values, and fenced tags.
- `Transcript` renders low agent prose apart from surrounding high prose.
- The normaliser keeps transcript markdown and removes tags from node summaries.

## Verification

- `pnpm --dir web test` — 69 files, 817 tests passed.
- `pnpm --dir web typecheck`
- `pnpm --dir web lint`
- `uv run pytest tests/test_orchestrator_normalise.py -k user_attention`
- `bin/lint`
- `bin/knip-check`

## Raised by review, not actioned

- Restrict attention parsing to top-level agent prose. The approved design requires unconditional
  parsing in the shared Markdown component, so this needs a separate design decision.
