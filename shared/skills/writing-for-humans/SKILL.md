---
name: writing-for-humans
description: Readable prose for a developer who does not know this codebase. Use when writing or editing a doc, README, CONTEXT.md, ADR, PR description, docstring or comment, or when another skill needs the plain-English writing rules. For skills, AGENTS.md and CLAUDE.md, use writing-for-agents instead.
---

Reference for writing any document a human reads — a guide in `docs/`, a README, an ADR, a PR
description, a docstring. The companion to [`writing-for-agents`](../writing-for-agents/SKILL.md).
That skill optimises for an agent taking the same process every run. These rules optimise for a
reader who skims, stops early, and does not hold the codebase in their head.

The reader is a competent developer who does not know this codebase. Explain the domain and the
decisions. Assume the language and the tools.

## Shape

Structure carries more readability than word choice does. Get the shape right first.

- **Lead with the why.** One sentence at the top of every document and section, saying what the
  thing is for. "This module reconciles inventory counts between the warehouse system and the
  storefront." The reader needs the purpose before the mechanism. For docstrings and comments the
  project's own language guide sets the bar — a comment that restates a well-named signature earns
  its place nowhere.
- **Order by decreasing need.** Summary, then details, then edge cases. A reader who stops after
  the first paragraph must leave with a correct — if incomplete — model, never a wrong one.
- **One idea per paragraph, one topic per section.** If a paragraph needs "and also", it is two
  paragraphs.
- **Keep the same sections in the same order** across documents of the same kind, so the reader
  skims by position. Reuse the shape already in the tree rather than inventing one.
- **State the reason a rule exists** when the rule is not obvious. A reader who knows why follows
  the rule in cases you did not write down.

## Sentences

Write ASD-STE100 (Simplified Technical English): short sentences, one instruction per sentence,
active voice, approved words in their approved meaning. Software vocabulary (commit, branch,
rebase, fixture, type check) and this project's own architecture are assumed, not explained.

On top of STE:

- **Average sentence under 20 words. No sentence over 30.** Count when a paragraph feels heavy.
- **Active voice, present tense.** "The parser rejects malformed input", not "malformed input will
  be rejected".
- **Repeat the noun** where a sentence would otherwise open with a bare "it", "this", or "these".
  Slightly repetitive prose beats prose the reader must backtrack through.
- **Prefer the concrete number.** "Retries 3 times with 2-second delays" beats "retries with
  backoff". Reach for the specific value, path, or command wherever one exists.
- **Expand each acronym on first use per document.**
- **Describe current behaviour only.** Changelog narration — "previously this did X, now it does
  Y" — belongs in migration notes and PR descriptions, nowhere else.

## Vocabulary

One name per concept, used everywhere. Drift between "user", "account", "customer", and "client"
for one idea forces the reader to work out whether the difference is meaningful.

`CONTEXT.md` is the source of truth for domain terms where the repo has one. Read that file
before you write, and reuse its terms verbatim, including its `_Avoid_` list. When `CONTEXT.md`
lacks a term you need, add the term there rather than defining it inline — see the
`domain-modeling` skill for the format.

Where the repo has no `CONTEXT.md`, take the vocabulary already used in `docs/` and in the code,
and stay consistent with it.

## Beyond prose

Some material reads better as something other than sentences. Reach for these first, then write
prose around what is left.

- **An example replaces a paragraph.** Two lines of real usage teach more than a careful
  description of the same thing. Every command, function and config key in user-facing reference
  docs gets one; elsewhere, add one where the signature alone does not show the usage.
- **A table carries anything with 3 or more parallel attributes** — parameters, error codes,
  config keys, options. Parallel facts in paragraph form force the reader to hold a table in their
  head anyway.
- **State preconditions and failure modes explicitly.** "Requires X to be initialised. Raises
  `TimeoutError` after 30s." Prose buries these; a reader debugging at 2am needs them findable.

## Comments describe the code, not the change

**The default is no comment.** Only a non-inferable constraint, a plausible-looking alternative that
was rejected, or a consequence invisible from the call site earns one.

A comment describes the code as it now stands. A multi-line rationale hung on a small edit describes
*the diff that produced it* — it reads as PR notes, and it ages into confusion once the change it
narrates is ancient history. Don't narrate the deliberation either: the reader needs the conclusion
and the constraint forcing it, not the alternatives you weighed on the way.

The migration story belongs in the commit message, where it stays discoverable without occupying the
file forever.

## Before you finish

Re-read the draft against this list, in order. Each pass hunts one thing.

1. **Named subjects.** Every sentence opens with a noun. Restore the noun wherever a bare
   pronoun stands in for one.
2. **Every word carries weight.** Cut the filler word or the whole sentence where it does not.
   Hedges and throat-clearing go first; the meaning survives them.
3. **Examples where usage is not obvious.** Each command, function and config key that needs one
   has one.
4. **Sentences within the cap.** Split anything over 30 words.
5. **One name per concept.** Every domain term matches `CONTEXT.md`, or the repo's existing
   vocabulary where no such file exists.
6. **Stale claims.** Every command, flag, path, and behaviour named — does the code still do that?
   Run the command or read the code. Documentation the reader cannot trust costs more than none.
