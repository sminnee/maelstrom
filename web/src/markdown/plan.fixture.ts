/**
 * The case the surface is tuned for: a plan document, read start to finish.
 * It is literal-dense on purpose — six or seven inline literals per paragraph,
 * most of them mid-sentence paths and symbol names — because that is the shape
 * that made the previous chip treatment shred the line, and it is the shape a
 * real plan takes. It also carries a heading with a literal inside it, which is
 * the case most likely to expose a clash between the display face and mono.
 */
export const planDocument = `## What already exists — extend, do not invent

Most of the machinery is built and spec'd. The framework work is one
generalisation of an existing mechanism (see "The real gap" below), not a new
subsystem.

- **Chaining.** \`Signature.next_actions\` (\`smartypants/services/job.py:71\`)
  carries a continuation that \`chain_task\` (\`smartypants/jobs/task_bootstrap.py:58-88\`)
  advances **only on the success path**. Spec: \`docs/specs/jobs/chaining.md\`.
- **The self-continuation precedent.** \`run_playbook_steps\`
  (\`smartypants/playbooks/tasks.py:22-74\`) already enqueues its own continuation
  and returns, freeing its worker slot, carrying loop state (\`prev_step_id\`,
  \`retry_count\`) forward as a runaway guard.
- **A durable per-job slot.** \`jobs.status\` is registered in the project schema
  (\`smartypants/app/db.py:43-59\`) and currently has no writer.
- **A resume cursor in integration settings.** \`PaginatedFetcher.fetch(..., resumable=True)\`
  (\`smartypants/integrations/support/fetcher.py:366-497\`) persists a page marker under
  \`fetch_resume_<data_type>\` with persist-after-store ordering.

### Three traps that will silently break a naive implementation

These are the reason this work needs planning rather than a direct attempt.

1. **A task that chains directly to itself with a stable \`unique_key\` is
   silently dropped.** \`kick\` skips a message whose \`unique_hash\` is already
   held (\`cancellable.py:222-236\`), and the hash is released only in
   \`_run_task_with_cleanup\`'s \`finally\` (\`cancellable.py:457-462\`) — *after*
   the body has run \`advance_chain\`.

   Every continuation must vary its \`unique_key\` (the cursor in \`args\` does this
   naturally, since \`perform_action_sig\`'s \`unique_key\` includes \`args\`).
2. **\`idempotency_key\` persists 25 hours past success** (\`IDEMPOTENCY_TTL\`). A
   continuation must never reuse one across chunks.
3. **A drain is invisible to the task body.** The only signal is
   \`asyncio.CancelledError\`, which is indistinguishable from a user cancel, a
   zombie reap and a max-age kill.

### The real gap: \`next_actions\` is static, not linear

The first framing of this plan proposed two new mechanisms — a continuation seam
and a fan-out primitive. That was wrong. Both are the same thing: **a job
deciding at runtime what comes next.**

\`\`\`python
def next_actions(self, result):
    if result.cursor is None:
        return []
    return [perform_action_sig(cursor=result.cursor)]
\`\`\`

> A useful consequence: each chained hop is a fresh task id with a fresh
> \`max_age_seconds\` window.

| Field | Owner | Notes |
| --- | --- | --- |
| \`jobs.status\` | schema | no writer yet |
| \`fetch_resume_<data_type>\` | settings | persist-after-store |
`;
