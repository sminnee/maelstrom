import { describe, expect, it } from 'vitest';

import { makeAgent, makeWorktree, worldWith } from '../fake/fixtures';
import { noWorktreeFilters, projectWorktrees } from './worktrees';

const natos = (rows: ReturnType<typeof projectWorktrees>) => rows.map((r) => r.worktree.nato);

describe('projectWorktrees', () => {
  it('lists only the asked project, by nato name', () => {
    const world = worldWith({
      worktrees: [
        makeWorktree({ id: 'acme-charlie', nato: 'charlie', project: 'acme' }),
        makeWorktree({ id: 'zed-alpha', nato: 'alpha', project: 'zed' }),
        makeWorktree({ id: 'acme-alpha', nato: 'alpha', project: 'acme' }),
      ],
    });
    expect(natos(projectWorktrees(world, 'acme', noWorktreeFilters()))).toEqual([
      'alpha',
      'charlie',
    ]);
  });

  it('puts _main first, because it is the checkout the others branch from', () => {
    const world = worldWith({
      worktrees: [
        makeWorktree({ id: 'acme-alpha', nato: 'alpha', project: 'acme' }),
        makeWorktree({ id: '_main', nato: '_main', project: 'acme' }),
      ],
    });
    expect(natos(projectWorktrees(world, 'acme', noWorktreeFilters()))).toEqual(['_main', 'alpha']);
  });

  it('hides closed worktrees unless asked', () => {
    const world = worldWith({
      worktrees: [
        makeWorktree({ id: 'acme-alpha', nato: 'alpha', project: 'acme' }),
        makeWorktree({ id: 'acme-bravo', nato: 'bravo', project: 'acme', isClosed: true }),
      ],
    });
    expect(natos(projectWorktrees(world, 'acme', noWorktreeFilters()))).toEqual(['alpha']);
    expect(natos(projectWorktrees(world, 'acme', { showClosed: true }))).toEqual([
      'alpha',
      'bravo',
    ]);
  });
});

describe('the tracked agents on a row', () => {
  const world = (agents: ReturnType<typeof makeAgent>[]) =>
    worldWith({
      worktrees: [makeWorktree({ id: 'acme-alpha', project: 'acme' })],
      agents,
    });

  const trackedOn = (agents: ReturnType<typeof makeAgent>[]) => {
    return projectWorktrees(world(agents), 'acme', noWorktreeFilters())[0]!.agents;
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
    expect(projectWorktrees(world, 'acme', noWorktreeFilters())[0]!.agents).toEqual([]);
  });
});
