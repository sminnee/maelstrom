---
name: memory-bolognese
description: Reduce a Claude memory store into verified repository docs, skills, and context. Invoked as `/memory-bolognese`.
disable-model-invocation: true
metadata:
  opencode/autoinvoke: false
  opencode/slash: true
---

# Memory bolognese

Verify every entry against current code before routing it. Give each entry one verdict:

- **DELETE**: stale or already documented; cite evidence.
- **MIGRATE**: durable codebase fact; name a destination and short form.
- **KEEP**: user-specific preference; explain why it is not repo knowledge.
- **FIX-IN-REPO**: a repo defect; fix it rather than preserving a workaround.

Parallelise verification and present one evidence-backed verdict table for approval. Then delete entries and their index lines together. Migrate by destination, one coherent commit per destination. Fix repo defects as ordinary changes, then remove their entries. Delete the scratch table and remeasure the index.

The index and store move together: each retained entry has one index line, and each deleted entry loses its line in the same commit. Verify this after any commit that changes either. If the index remains over its threshold after the sweep, report it; do not delete genuine user preferences to meet a number.

Put facts needed always in concise context. Put subsystem facts in the matching skill or README. Put cross-project patterns in the mael wiki. Edit cross-project skills in `shared/skills/`, never their installed copy. Keep memories about the user; put durable codebase facts in the repository when learned.

Give gotchas an existing, relevant destination before creating a new file. Working-style feedback belongs in cross-project skills, not a project memory store.
