import type { ChipTone } from '../protocol/chipTone';
import type { PrState } from '../protocol/entities';

/** A document's status in words: `awaiting-review` becomes "awaiting review". */
export function describeDocumentStatus(status: string): string {
  return status.replace(/-/g, ' ');
}

/** A PR's state in words, for the link that opens it. */
const PR_STATES: Record<PrState | 'draft', string> = {
  draft: 'draft',
  merged: 'merged',
  'ci-failed': 'CI failed',
  'ci-running': 'CI running',
  conflict: 'merge conflicts',
  'checks-unreadable': 'checks not readable',
  unknown: 'checking',
  ready: 'ready to merge',
};

/**
 * The state a PR reads as, which is `draft` before anything else.
 *
 * A draft's checks and mergeability are not what a reader wants to know about
 * a PR nobody is asked to merge yet. The label and the chip's dot both read
 * from here, so the precedence is decided once.
 */
export function prStateKey(state: PrState | '', isDraft: boolean): PrState | 'draft' | '' {
  if (isDraft) return 'draft';
  return state;
}

/** A PR's state in words: `CI running`. The caller assembles the reading. */
export function describePrState(state: PrState | '', isDraft: boolean): string {
  // `?? ''` guards the wire, not the type: `prState` crosses as a plain string
  // from Python, so a value outside the union must draw nothing rather than
  // name a state it never had.
  const key = prStateKey(state, isDraft);
  return key ? (PR_STATES[key] ?? '') : '';
}

/**
 * The reading a chip colours a PR by, mapping this domain onto the six tones.
 *
 * `ci-failed` and `conflict` share `bad` on purpose — both are the same demand
 * on the operator, and the chip's icon is what tells them apart. `merged` and
 * `ready` part: settled is not the same as your turn.
 *
 * `checks-unreadable` is `quiet` for the reason a stale usage chip is: the app
 * has no reading to stand behind, so it must not draw one in a colour that
 * claims otherwise. It is not `neutral`, which `unknown` uses to say an answer
 * is still coming — here none is.
 */
const PR_TONES: Record<PrState | 'draft', ChipTone> = {
  draft: 'quiet',
  merged: 'special',
  'ci-failed': 'bad',
  'ci-running': 'busy',
  conflict: 'bad',
  'checks-unreadable': 'quiet',
  unknown: 'neutral',
  ready: 'good',
};

/** The tone a PR reads as. An unreadable state reports no state, not a wrong one. */
export function prTone(state: PrState | '', isDraft: boolean): ChipTone {
  const key = prStateKey(state, isDraft);
  return (key && PR_TONES[key]) || 'neutral';
}
