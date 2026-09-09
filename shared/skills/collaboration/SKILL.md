---
name: collaboration
description: Work with the user when a test or CI run fails, a review finding adds machinery, or a decision needs escalation.
---

# Collaboration

Own failures from this session. Diagnose and fix them. Do not dismiss a failure as pre-existing or unrelated. To verify a baseline, inspect the relevant path in `origin/main`; never stash work merely to compare it.

Ask before re-running CI solely to clear a suspected flake. State the evidence and offer a rerun, trace inspection, or a check of `main`.

For scale concerns, state the volume where the problem matters and compare it with current volume. Prefer an existing, small mechanism over new machinery unless the gap is material.

Raise dangerous shortcuts as a question when the user requires approval. Do not place them in an execution plan.
