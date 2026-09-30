import { describe, expect, it } from 'vitest';

import type { NodeState } from '../protocol/progress';
import { makeAgent, makeTask, makeWorktree } from '../test/fixtures';
import { cardPr, type CardPrFacts } from './cardPr';

const STARTED = '2026-09-21T10:00:00+00:00';

function node(state: NodeState, over: Partial<CardPrFacts> = {}): CardPrFacts {
  return {
    kind: 'task',
    task: makeTask(),
    agent: undefined,
    worktree: makeWorktree({ prNumber: 42, prState: 'ready' }),
    progress: { state, words: '', drift: null, fixStatus: null, echoesStatus: false },
    ...over,
  };
}

const merged = (prMergedAt: string) =>
  makeWorktree({ prNumber: 42, prState: 'merged', prMergedAt });

describe('cardPr', () => {
  it('shows no PR on a card that has not started, though its branch has an open one', () => {
    expect(cardPr(node('ready'))).toBeUndefined();
    expect(cardPr(node('queued'))).toBeUndefined();
  });

  it('hides a PR that merged before the card’s agent started', () => {
    const card = node('working', {
      agent: makeAgent({ startedAt: STARTED }),
      worktree: merged('2026-09-20T10:00:00Z'),
    });
    expect(cardPr(card)).toBeUndefined();
  });

  it('shows a PR that merged after the card’s agent started', () => {
    const worktree = merged('2026-09-22T10:00:00Z');
    const card = node('finalising', { agent: makeAgent({ startedAt: STARTED }), worktree });
    expect(cardPr(card)).toBe(worktree);
  });

  it('shows an open PR to a card with an agent: a branch has at most one', () => {
    const card = node('working', { agent: makeAgent({ startedAt: STARTED }) });
    expect(cardPr(card)).toBe(card.worktree);
  });

  it('shows the merged PR on a finished card with no start time', () => {
    const card = node('done', { worktree: merged('2026-09-20T10:00:00Z') });
    expect(cardPr(card)).toBe(card.worktree);
  });

  it('hides a PR that merged before the task started, once its agent has gone', () => {
    const card = node('done', {
      task: makeTask({ startedAt: STARTED }),
      worktree: merged('2026-09-20T10:00:00Z'),
    });
    expect(cardPr(card)).toBeUndefined();
  });

  it('keeps the PR a re-run task’s first agent made', () => {
    const worktree = merged('2026-09-22T10:00:00Z');
    const card = node('working', {
      task: makeTask({ startedAt: STARTED }),
      agent: makeAgent({ startedAt: '2026-09-23T10:00:00+00:00' }),
      worktree,
    });
    expect(cardPr(card)).toBe(worktree);
  });

  it('shows the merged PR when either time is unknown', () => {
    const unknownStart = node('working', {
      agent: makeAgent({ startedAt: '' }),
      worktree: merged('2026-09-20T10:00:00Z'),
    });
    expect(cardPr(unknownStart)).toBe(unknownStart.worktree);
    const unknownMerge = node('working', {
      agent: makeAgent({ startedAt: STARTED }),
      worktree: merged(''),
    });
    expect(cardPr(unknownMerge)).toBe(unknownMerge.worktree);
  });

  it('shows a PR that merged the moment the agent started', () => {
    const worktree = merged(STARTED);
    const card = node('working', { agent: makeAgent({ startedAt: STARTED }), worktree });
    expect(cardPr(card)).toBe(worktree);
  });

  it('applies the rule to the worktree the caller passes', () => {
    const card = node('working', { agent: makeAgent({ startedAt: STARTED }), worktree: undefined });
    expect(cardPr(card, merged('2026-09-20T10:00:00Z'))).toBeUndefined();
  });

  it('leaves a free agent’s PR alone', () => {
    const card = node('working', { kind: 'freeAgent', worktree: merged('2026-09-20T10:00:00Z') });
    expect(cardPr({ ...card, agent: makeAgent({ startedAt: STARTED }) })).toBe(card.worktree);
  });
});
