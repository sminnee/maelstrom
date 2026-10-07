import { describe, expect, it } from 'vitest';

import { makeTask, makeWorktree } from '../fake/fixtures';
import type { TaskRow } from '../api/types';
import { cardPr, chainRegistrations, registeredPr as resolve, type CardPrFacts } from './cardPr';

/** `task`'s registration, among `tasks`. */
const registeredPr = (task: TaskRow, tasks: TaskRow[]) => resolve(task, chainRegistrations(tasks));

const URL_42 = 'https://github.com/o/r/pull/42';

function node(over: Partial<CardPrFacts> = {}): CardPrFacts {
  return {
    registeredPr: { number: 42, url: URL_42 },
    worktree: makeWorktree({ prNumber: 42, prState: 'ready', prUrl: URL_42 }),
    ...over,
  };
}

describe('registeredPr', () => {
  const parent = 'NORT-1';

  it('is the task’s own registration', () => {
    const own = makeTask({ prNumber: 42, prUrl: URL_42 });
    expect(registeredPr(own, [own])).toEqual({ number: 42, url: URL_42 });
  });

  it('falls back to a chain sibling’s registration', () => {
    const sibling = makeTask({ id: 'NORT-1.1', parent, prNumber: 42, prUrl: URL_42 });
    const next = makeTask({ id: 'NORT-1.2', parent });
    expect(registeredPr(next, [sibling, next])).toEqual({ number: 42, url: URL_42 });
  });

  it('counts the chain’s root task as part of the chain', () => {
    const root = makeTask({ id: 'NORT-1', notebookId: parent, prNumber: 42, prUrl: URL_42 });
    const child = makeTask({ id: 'NORT-1.1', parent });
    expect(registeredPr(child, [root, child])?.number).toBe(42);
  });

  it('takes the latest PR of the chain, by number', () => {
    const old = makeTask({ id: 'NORT-1.1', parent, prNumber: 41, prUrl: 'u41' });
    const newer = makeTask({ id: 'NORT-1.2', parent, prNumber: 42, prUrl: URL_42 });
    const next = makeTask({ id: 'NORT-1.3', parent });
    expect(registeredPr(next, [newer, old, next])?.number).toBe(42);
    expect(registeredPr(next, [old, newer, next])?.number).toBe(42);
  });

  it('prefers the task’s own registration over a later one in the chain', () => {
    const own = makeTask({ id: 'NORT-1.1', parent, prNumber: 41, prUrl: 'u41' });
    const sibling = makeTask({ id: 'NORT-1.2', parent, prNumber: 42, prUrl: URL_42 });
    expect(registeredPr(own, [own, sibling])?.number).toBe(41);
  });

  it('is none when nothing in the chain registered a PR', () => {
    const task = makeTask({ parent });
    expect(registeredPr(task, [task, makeTask({ id: 'other', prNumber: 9 })])).toBeUndefined();
  });

  it('does not cross projects that share a parent name', () => {
    const elsewhere = makeTask({ id: 'p2/NORT-1.1', project: 'p2', parent, prNumber: 42 });
    const task = makeTask({ id: 'NORT-1.2', parent });
    expect(registeredPr(task, [elsewhere, task])).toBeUndefined();
  });
});

describe('cardPr', () => {
  it('reads the worktree’s state when it holds the registered PR', () => {
    expect(cardPr(node())).toEqual({ number: 42, url: URL_42, state: 'ready', draft: false });
  });

  it('shows no PR when nothing registered one, though the branch has one', () => {
    expect(cardPr(node({ registeredPr: undefined }))).toBeUndefined();
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
    expect(cardPr(node({ registeredPr: { number: 42, url: '' } }))?.url).toBe(URL_42);
  });

  it('draws a same-numbered PR of another repo with no state', () => {
    const elsewhere = { number: 42, url: 'https://github.com/other/repo/pull/42' };
    expect(cardPr(node({ registeredPr: elsewhere }))?.state).toBe('');
  });
});
