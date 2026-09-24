import { describe, expect, it } from 'vitest';

import { makeAgent, makeWorktree, worldWith } from '../test/fixtures';
import { listWorktrees, noWorktreeFilters } from './worktrees';

const filters = { project: null, branch: null };

describe('listWorktrees', () => {
  it('groups worktrees by project, projects in name order', () => {
    const world = worldWith({
      worktrees: [
        makeWorktree({ id: 'zed-alpha', project: 'zed', nato: 'alpha' }),
        makeWorktree({ id: 'acme-alpha', project: 'acme', nato: 'alpha' }),
      ],
    });
    const groups = listWorktrees(world, filters, noWorktreeFilters());
    expect(groups.map((g) => g.project)).toEqual(['acme', 'zed']);
  });

  it('orders worktrees within a project by their nato name', () => {
    const world = worldWith({
      worktrees: [
        makeWorktree({ id: 'acme-charlie', nato: 'charlie', project: 'acme' }),
        makeWorktree({ id: 'acme-alpha', nato: 'alpha', project: 'acme' }),
        makeWorktree({ id: 'acme-bravo', nato: 'bravo', project: 'acme' }),
      ],
    });
    const [group] = listWorktrees(world, filters, noWorktreeFilters());
    expect(group!.rows.map((r) => r.worktree.nato)).toEqual(['alpha', 'bravo', 'charlie']);
  });

  it('puts _main first, because it is the checkout the others branch from', () => {
    const world = worldWith({
      worktrees: [
        makeWorktree({ id: 'acme-alpha', nato: 'alpha', project: 'acme' }),
        makeWorktree({ id: '_main', nato: '_main', project: 'acme' }),
      ],
    });
    const [group] = listWorktrees(world, filters, noWorktreeFilters());
    expect(group!.rows.map((r) => r.worktree.nato)).toEqual(['_main', 'alpha']);
  });

  it('hides closed worktrees by default', () => {
    const world = worldWith({
      worktrees: [
        makeWorktree({ id: 'acme-alpha', nato: 'alpha', project: 'acme' }),
        makeWorktree({ id: 'acme-bravo', nato: 'bravo', project: 'acme', isClosed: true }),
      ],
    });
    const [group] = listWorktrees(world, filters, noWorktreeFilters());
    expect(group!.rows.map((r) => r.worktree.nato)).toEqual(['alpha']);
  });

  it('shows closed worktrees when asked', () => {
    const world = worldWith({
      worktrees: [
        makeWorktree({ id: 'acme-alpha', nato: 'alpha', project: 'acme' }),
        makeWorktree({ id: 'acme-bravo', nato: 'bravo', project: 'acme', isClosed: true }),
      ],
    });
    const [group] = listWorktrees(world, filters, { showClosed: true });
    expect(group!.rows.map((r) => r.worktree.nato)).toEqual(['alpha', 'bravo']);
  });

  it('drops a project whose every worktree is filtered out', () => {
    const world = worldWith({
      worktrees: [makeWorktree({ id: 'acme-alpha', project: 'acme', isClosed: true })],
    });
    expect(listWorktrees(world, filters, noWorktreeFilters())).toEqual([]);
  });

  it('honours the project filter', () => {
    const world = worldWith({
      worktrees: [
        makeWorktree({ id: 'acme-alpha', project: 'acme' }),
        makeWorktree({ id: 'zed-alpha', project: 'zed' }),
      ],
    });
    const groups = listWorktrees(world, { ...filters, project: 'zed' }, noWorktreeFilters());
    expect(groups.map((g) => g.project)).toEqual(['zed']);
  });
});

describe('the tracked agents on a row', () => {
  const world = (agents: ReturnType<typeof makeAgent>[]) =>
    worldWith({
      worktrees: [makeWorktree({ id: 'acme-alpha', project: 'acme' })],
      agents,
    });

  const trackedOn = (agents: ReturnType<typeof makeAgent>[]) => {
    const [group] = listWorktrees(world(agents), filters, noWorktreeFilters());
    return group!.rows[0]!.agents;
  };

  it('counts an agent working in the worktree', () => {
    const tracked = trackedOn([
      makeAgent({ id: 'a1', worktreeId: 'acme-alpha', state: 'processing' }),
    ]);
    expect(tracked.map((a) => a.id)).toEqual(['a1']);
  });

  it('ignores an agent in another worktree', () => {
    const tracked = trackedOn([
      makeAgent({ id: 'a1', worktreeId: 'acme-bravo', state: 'processing' }),
    ]);
    expect(tracked).toEqual([]);
  });

  it('ignores a subagent, which runs in its parent’s worktree', () => {
    const tracked = trackedOn([
      makeAgent({ id: 'a1', worktreeId: 'acme-alpha', state: 'processing', parent: 'a0' }),
    ]);
    expect(tracked).toEqual([]);
  });

  it('ignores an exited agent, which is a row lingering in the world', () => {
    const tracked = trackedOn([makeAgent({ id: 'a1', worktreeId: 'acme-alpha', state: 'exited' })]);
    expect(tracked).toEqual([]);
  });

  it('is not the session count, which is a process sweep', () => {
    const world = worldWith({
      worktrees: [makeWorktree({ id: 'acme-alpha', project: 'acme', sessionCount: 3 })],
      agents: [],
    });
    const [group] = listWorktrees(world, filters, noWorktreeFilters());
    expect(group!.rows[0]!.agents).toEqual([]);
  });
});
