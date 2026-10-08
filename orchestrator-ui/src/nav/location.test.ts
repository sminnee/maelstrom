import { describe, expect, it } from 'vitest';
import { changesTab, devEnvTab, documentTab, sessionTab } from '../selectors/tabs';
import { defaultLoc, parseLocation, toHref, withLoc, type LocPatch } from './location';

const parse = (href: string) => {
  const url = new URL(href, 'http://x');
  return parseLocation(url.pathname, url.search);
};

describe('the URL of a location', () => {
  it.each<[string, LocPatch]>([
    ['/desk', {}],
    ['/tasks', { view: 'list' }],
    ['/worktrees', { view: 'worktrees' }],
    ['/desk/task/NORT-12', { card: { kind: 'task', id: 'NORT-12' } }],
    // A task id holds `/`: the task path takes every segment after it.
    ['/desk/task/maelstrom/2026-09-22.1', { card: { kind: 'task', id: 'maelstrom/2026-09-22.1' } }],
    ['/desk/agent/c3e8f1b5', { card: { kind: 'agent', id: 'c3e8f1b5' } }],
    ['/desk/worktree/northwind-alpha', { card: { kind: 'worktree', id: 'northwind-alpha' } }],
    ['/desk/agent/a%2Fb', { card: { kind: 'agent', id: 'a/b' } }],
    ['/desk?panel=session/c3e8f1b5', { panel: sessionTab('c3e8f1b5') }],
    ['/desk?panel=document/doc-1', { panel: documentTab('doc-1') }],
    [
      '/tasks?panel=changes/northwind-delta',
      { view: 'list', panel: changesTab('northwind-delta') },
    ],
    ['/desk?panel=devenv/northwind-delta/web', { panel: devEnvTab('northwind-delta', 'web') }],
    [
      '/desk?project=northwind&branch=northwind/main',
      { filters: { project: 'northwind', branch: 'northwind/main' } },
    ],
    ['/desk?agents=working-idle', { filters: { agentStatus: 'working-idle' } }],
    [
      '/tasks?status=done,cancelled',
      { view: 'list', listFilters: { statuses: ['done', 'cancelled'] } },
    ],
    ['/tasks?status=all', { view: 'list', listFilters: { statuses: [] } }],
    ['/tasks?q=fix%20%26%20test', { view: 'list', listFilters: { text: 'fix & test' } }],
    ['/worktrees?closed=1', { view: 'worktrees', worktreeFilters: { showClosed: true } }],
    ['/desk?zone=done', { zone: 'done' }],
    ['/tasks?edit=maelstrom/2026-09-22.1', { view: 'list', edit: 'maelstrom/2026-09-22.1' }],
    ['/desk?new=1', { newWork: true }],
  ])('%s', (href, patch) => {
    const loc = withLoc(defaultLoc(), patch);
    expect(toHref(loc)).toBe(href);
    expect(parse(href)).toEqual(loc);
  });

  it('leaves out every default', () => {
    const loc = withLoc(defaultLoc(), {
      filters: { agentStatus: 'all' },
      listFilters: { statuses: ['blocked', 'todo', 'in-progress'], text: '' },
      zone: 'running',
    });
    expect(toHref(loc)).toBe('/desk');
  });

  it('has no screen for an unknown path', () => {
    expect(parse('/')).toBeNull();
    expect(parse('/nowhere')).toBeNull();
    expect(parse('/tasks/NORT-12')).toBeNull();
    expect(parse('/desk/agent/a/b')).toBeNull();
  });

  it('reads a value it does not know as the default', () => {
    expect(parse('/desk?zone=sideways&agents=asleep&panel=nothing/x&status=bogus')).toEqual(
      defaultLoc(),
    );
  });
});

describe('withLoc', () => {
  it('moves to the desk to open a card, and drops the card on leaving it', () => {
    const list = withLoc(defaultLoc(), { view: 'list' });
    const card = withLoc(list, { card: { kind: 'task', id: 'NORT-12' } });
    expect(card.view).toBe('canvas');
    expect(withLoc(card, { view: 'worktrees' }).card).toBeNull();
  });

  it('merges a filter into the others', () => {
    const loc = withLoc(defaultLoc(), { filters: { project: 'northwind' } });
    expect(withLoc(loc, { filters: { agentStatus: 'idle' } }).filters).toEqual({
      project: 'northwind',
      branch: null,
      agentStatus: 'idle',
    });
  });
});
