import { describe, expect, it } from 'vitest';

import { makeTask, makeWorktree } from '../fake/fixtures';
import { cardPr, type CardPrFacts } from './cardPr';

const URL_42 = 'https://github.com/o/r/pull/42';

function node(over: Partial<CardPrFacts> = {}): CardPrFacts {
  return {
    task: makeTask({ prNumber: 42, prUrl: URL_42 }),
    worktree: makeWorktree({ prNumber: 42, prState: 'ready', prUrl: URL_42 }),
    ...over,
  };
}

describe('cardPr', () => {
  it('reads the worktree’s state when it holds the registered PR', () => {
    expect(cardPr(node())).toEqual({ number: 42, url: URL_42, state: 'ready', draft: false });
  });

  it('shows no PR on a free agent, which has no task', () => {
    expect(cardPr(node({ task: undefined }))).toBeUndefined();
  });

  it('shows no PR when nothing registered one, though the branch has one', () => {
    expect(cardPr(node({ task: makeTask({ prNumber: 0, prUrl: '' }) }))).toBeUndefined();
  });

  it('draws a registered PR the worktree does not hold with no state', () => {
    const worktree = makeWorktree({ prNumber: 50, prState: 'merged' });
    expect(cardPr(node({ worktree }))).toEqual({
      number: 42,
      url: URL_42,
      state: '',
      draft: false,
    });
  });

  it('draws a registered PR with no worktree with no state', () => {
    expect(cardPr(node({ worktree: undefined }))?.state).toBe('');
  });

  it('reads the state off the worktree the caller passes', () => {
    const passed = makeWorktree({ prNumber: 42, prState: 'merged' });
    expect(cardPr(node({ worktree: undefined }), passed)?.state).toBe('merged');
  });

  it('takes the worktree’s URL when the registration has none', () => {
    // A bare number registered with no repo URL to build one from.
    expect(cardPr(node({ task: makeTask({ prNumber: 42, prUrl: '' }) }))?.url).toBe(URL_42);
  });

  it('draws a same-numbered PR of another repo with no state', () => {
    const elsewhere = makeTask({ prNumber: 42, prUrl: 'https://github.com/other/repo/pull/42' });
    expect(cardPr(node({ task: elsewhere }))?.state).toBe('');
  });
});
