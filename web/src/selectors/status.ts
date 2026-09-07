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

/** How a PR link reads after its number: `PR #278 · CI running`. */
export function describePrState(state: PrState | '', isDraft: boolean): string {
  // `?? ''` guards the wire, not the type: `prState` crosses as a plain string
  // from Python, so a value outside the union must draw nothing rather than
  // render "PR #278 · undefined".
  const key = prStateKey(state, isDraft);
  return key ? (PR_STATES[key] ?? '') : '';
}
