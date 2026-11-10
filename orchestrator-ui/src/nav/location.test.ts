import { describe, expect, it } from 'vitest';
import { changesTab, devEnvTab, documentTab, sessionTab } from '../selectors/tabs';
import { defaultLoc, parseLocation, toHref, withLoc, type LocPatch } from './location';

const parse = (href: string) => {
  const url = new URL(href, 'http://x');
  return parseLocation(url.pathname, url.search);
};

describe('the URL of a location', () => {
  it.each<[string, LocPatch]>([
    ['/desk/graph', {}],
    ['/tasks', { view: 'list' }],
    ['/comms', { view: 'comms' }],
    ['/desk/graph/task/NORT-12', { card: { kind: 'task', id: 'NORT-12' } }],
    // A task id holds `/`: the task path takes every segment after it.
    [
      '/desk/graph/task/maelstrom/2026-09-22.1',
      { card: { kind: 'task', id: 'maelstrom/2026-09-22.1' } },
    ],
    ['/desk/graph/agent/c3e8f1b5', { card: { kind: 'agent', id: 'c3e8f1b5' } }],
    ['/desk/graph/worktree/northwind-alpha', { card: { kind: 'worktree', id: 'northwind-alpha' } }],
    ['/desk/graph/agent/a%2Fb', { card: { kind: 'agent', id: 'a/b' } }],
    ['/desk/graph?panel=session/c3e8f1b5', { panel: sessionTab('c3e8f1b5') }],
    ['/desk/graph?panel=document/doc-1', { panel: documentTab('doc-1') }],
    [
      '/tasks?panel=changes/northwind-delta',
      { view: 'list', panel: changesTab('northwind-delta') },
    ],
    [
      '/desk/graph?panel=devenv/northwind-delta/web',
      { panel: devEnvTab('northwind-delta', 'web') },
    ],
    [
      '/desk/graph?project=northwind&branch=northwind/main',
      { filters: { project: 'northwind', branch: 'northwind/main' } },
    ],
    ['/desk/graph?agents=working-idle', { filters: { agentStatus: 'working-idle' } }],
    [
      '/tasks?status=done,cancelled',
      { view: 'list', listFilters: { statuses: ['done', 'cancelled'] } },
    ],
    ['/tasks?status=all', { view: 'list', listFilters: { statuses: [] } }],
    ['/tasks?q=fix%20%26%20test', { view: 'list', filters: { text: 'fix & test' } }],
    ['/tasks?comm=c2', { view: 'list', filters: { comm: 'c2' } }],
    [
      '/projects?detail=northwind&dtab=worktrees&closed=1',
      {
        view: 'projects',
        projectDetail: 'northwind',
        projectTab: 'worktrees',
        worktreeFilters: { showClosed: true },
      },
    ],
    ['/desk/graph?zone=done', { zone: 'done' }],
    ['/desk/list?zone=queued', { mode: 'list', zone: 'queued' }],
    ['/desk/kanban', { mode: 'kanban' }],
    ['/desk/kanban/task/NORT-12', { mode: 'kanban', card: { kind: 'task', id: 'NORT-12' } }],
    ['/desk/list/agent/c3e8f1b5', { mode: 'list', card: { kind: 'agent', id: 'c3e8f1b5' } }],
    [
      '/desk/list/worktree/northwind-alpha',
      { mode: 'list', card: { kind: 'worktree', id: 'northwind-alpha' } },
    ],
    ['/tasks?edit=maelstrom/2026-09-22.1', { view: 'list', edit: 'maelstrom/2026-09-22.1' }],
    ['/projects?detail=northwind', { view: 'projects', projectDetail: 'northwind' }],
    ['/desk/graph?new=1', { newWork: true }],
    ['/desk/graph?welcome=1', { welcome: true }],
    ['/projects?add=1', { view: 'projects', addProject: true }],
  ])('%s', (href, patch) => {
    const loc = withLoc(defaultLoc(), patch);
    expect(toHref(loc)).toBe(href);
    expect(parse(href)).toEqual(loc);
  });

  it('leaves out every default', () => {
    const loc = withLoc(defaultLoc(), {
      filters: { agentStatus: 'all', text: '' },
      listFilters: { statuses: ['blocked', 'todo', 'in-progress'] },
      zone: 'running',
    });
    expect(toHref(loc)).toBe('/desk/graph');
  });

  it('has no screen for an unknown path', () => {
    expect(parse('/')).toBeNull();
    expect(parse('/nowhere')).toBeNull();
    expect(parse('/tasks/NORT-12')).toBeNull();
    expect(parse('/desk/graph/agent/a/b')).toBeNull();
    // The desk itself has no screen: a route redirects it to a mode.
    expect(parse('/desk')).toBeNull();
    expect(parse('/desk/sideways')).toBeNull();
    // No Worktrees view: the worktree table is a Project dialog tab.
    expect(parse('/worktrees')).toBeNull();
  });

  it('reads a task path with no id as the desk', () => {
    expect(parse('/desk/graph/task')).toEqual(defaultLoc());
  });

  it('reads the one old zone of work not started as to do', () => {
    expect(parse('/desk/list?zone=notStarted')?.zone).toBe('todo');
  });

  it('leaves out the dialog tab and its table filter once the dialog is closed', () => {
    const open = withLoc(defaultLoc(), {
      projectDetail: 'northwind',
      projectTab: 'worktrees',
      worktreeFilters: { showClosed: true },
    });
    expect(toHref(withLoc(open, { projectDetail: null }))).toBe('/desk/graph');
  });

  it('reads a value it does not know as the default', () => {
    expect(
      parse('/desk/graph?zone=sideways&agents=asleep&panel=nothing/x&status=bogus&dtab=nope'),
    ).toEqual(defaultLoc());
  });
});

describe('withLoc', () => {
  it('moves to the desk to open a card, and drops the card on leaving it', () => {
    const list = withLoc(defaultLoc(), { view: 'list' });
    const card = withLoc(list, { card: { kind: 'task', id: 'NORT-12' } });
    expect(card.view).toBe('desk');
    expect(withLoc(card, { view: 'comms' }).card).toBeNull();
  });

  it('keeps the desk mode when a card opens, and links back to it from another view', () => {
    const kanban = withLoc(defaultLoc(), { mode: 'kanban' });
    expect(toHref(withLoc(kanban, { card: { kind: 'task', id: 'NORT-12' } }))).toBe(
      '/desk/kanban/task/NORT-12',
    );
    // Off the desk the search holds the mode, so the URL round trip keeps it.
    const comms = parse(toHref(withLoc(kanban, { view: 'comms' })))!;
    expect(toHref(comms)).toBe('/comms?desk=kanban');
    expect(toHref(withLoc(comms, { view: 'desk' }))).toBe('/desk/kanban');
  });

  it('merges a filter into the others', () => {
    const loc = withLoc(defaultLoc(), { filters: { project: 'northwind' } });
    expect(withLoc(loc, { filters: { agentStatus: 'idle' } }).filters).toEqual({
      project: 'northwind',
      branch: null,
      agentStatus: 'idle',
      text: '',
      comm: null,
    });
  });
});
