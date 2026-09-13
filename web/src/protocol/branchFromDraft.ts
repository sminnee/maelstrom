/**
 * Naming a task from its prose alone, with no inference call.
 *
 * The dialog's Suggest button asks the server, which asks a model. These are
 * what a save that never pressed it uses instead, so a task always reaches the
 * notebook with a title and a branch rather than depending on the user typing
 * them.
 *
 * Both mirror the *deterministic* half of `src/maelstrom/branch_name.py` —
 * `_first_line` and `slugify` — never the model's half. It is the same shape the
 * notebook itself falls back to when the `claude` CLI is missing.
 *
 * One limit is shared with Python rather than introduced here: `slugify` keeps
 * `[a-z0-9]` only, so prose in a non-Latin script slugs to nothing and every
 * such draft falls back to `feat/task`. Fixing that means moving both sides.
 */

/** The stopwords `_STOPWORDS` holds, so the kept words carry the meaning of the work. */
const STOPWORDS = new Set([
  'a',
  'an',
  'and',
  'are',
  'as',
  'at',
  'be',
  'by',
  'for',
  'from',
  'in',
  'into',
  'is',
  'it',
  'of',
  'on',
  'or',
  'the',
  'to',
  'with',
  'this',
  'that',
  'these',
  'those',
  'via',
  'vs',
]);

/** How many meaningful words a branch's description keeps. */
const SLUG_WORDS = 4;

/** Where inference caps a title, so the two agree on what a long draft names. */
const TITLE_LIMIT = 80;

/**
 * The draft's first non-empty line, capped. What inference returns for a title
 * too: the prose's opening line is what the user already wrote as the summary.
 *
 * Non-empty, not first: a draft that opens with a blank line is ordinary, from a
 * paste or an Enter pressed before typing, and the literal first line of one is
 * `''` -- which would write a task with no title at all. `_first_line` skips
 * those, so this does.
 *
 * Capped then trimmed, in that order, because `_first_line` ends on `rstrip`:
 * the other order leaves the space a cap landing mid-word exposes.
 */
export function titleFromDraft(draft: string): string {
  for (const line of draft.split('\n')) {
    const stripped = line.trim();
    if (stripped) return stripped.slice(0, TITLE_LIMIT).trimEnd();
  }
  return '';
}

/** `slugify`: lowercase, drop punctuation and stopwords, keep the first few. */
function slugify(text: string, maxWords: number): string {
  const words = text.toLowerCase().match(/[a-z0-9]+/g) ?? [];
  const kept = words.filter((w) => !STOPWORDS.has(w));
  // A title made entirely of stopwords falls back to the raw words, so
  // something is still named — as Python's own fallback does.
  return (kept.length > 0 ? kept : words).slice(0, maxWords).join('-');
}

/**
 * A `feat/<desc>` branch for a draft, matching the notebook's fallback shape.
 *
 * `feat` because a new task with nothing said about it is a feature by default,
 * which is `generate_branch_name`'s own `default_type`.
 */
export function branchFromDraft(draft: string): string {
  return `feat/${slugify(titleFromDraft(draft), SLUG_WORDS) || 'task'}`;
}
