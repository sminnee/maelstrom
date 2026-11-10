import { describe, expect, it } from 'vitest';

import { makeAgent, makeWorktree, worldWith } from '../fake/fixtures';
import {
  noWorktreeFilters,
  parentName,
  projectWorktrees,
  usedByParent,
  worktreeName,
} from './worktrees';

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

describe('worktreeName', () => {
  it('keeps the bare nato for a worktree this server owns', () => {
    const own = makeWorktree({ id: 'acme-bravo', nato: 'bravo' });
    expect(worktreeName(own, '')).toBe('bravo');
  });

  it('keeps the bare nato on a server whose own ids carry a prefix', () => {
    const own = makeWorktree({ id: 'cobalt:acme-bravo', nato: 'bravo' });
    expect(worktreeName(own, 'cobalt')).toBe('bravo');
  });

  it('keeps the bare nato for a bare id on a server with a prefix', () => {
    const bare = makeWorktree({ id: 'acme-bravo', nato: 'bravo' });
    expect(worktreeName(bare, 'cobalt')).toBe('bravo');
  });

  it('names the owner of a worktree another host owns', () => {
    const merged = makeWorktree({ id: 'cobalt:acme-bravo', nato: 'bravo' });
    expect(worktreeName(merged, '')).toBe('cobalt:bravo');
  });
});

describe('parentName', () => {
  it('is the first DNS label of the URL host', () => {
    expect(parentName('https://monolith.tailbaef49.ts.net:2772')).toBe('monolith');
  });

  it('is the whole host for an IP address', () => {
    expect(parentName('https://100.64.0.7:2772')).toBe('100.64.0.7');
  });

  it('is empty for a URL that does not parse', () => {
    expect(parentName('not a url')).toBe('');
  });
});

describe('usedByParent', () => {
  const PARENT = 'https://monolith.tailbaef49.ts.net:2772';
  const worktree = makeWorktree({ id: 'acme-bravo', nato: 'bravo' });
  const agentIn = (over: Parameters<typeof makeAgent>[0]) =>
    makeAgent({ worktreeId: 'acme-bravo', parentUrl: PARENT, ...over });

  it('names the parent of a live delegated agent', () => {
    const world = worldWith({ worktrees: [worktree], agents: [agentIn({})] });
    expect(usedByParent(world, worktree, '')).toBe('monolith');
  });

  it('names the parent of the newest delegated agent', () => {
    const older = agentIn({ id: 'old', startedAt: '2026-06-11T09:00:00Z' });
    const newer = agentIn({
      id: 'new',
      startedAt: '2026-06-11T10:00:00Z',
      parentUrl: 'https://zinc.tailbaef49.ts.net:2772',
    });
    const world = worldWith({ worktrees: [worktree], agents: [older, newer] });
    expect(usedByParent(world, worktree, '')).toBe('zinc');
  });

  it.each([
    ['an exited agent', agentIn({ state: 'exited' })],
    ['a subagent', agentIn({ parent: 'agent-0' })],
    ['a direct agent', agentIn({ parentUrl: '' })],
  ])('ignores %s', (_label, agent) => {
    const world = worldWith({ worktrees: [worktree], agents: [agent] });
    expect(usedByParent(world, worktree, '')).toBe('');
  });

  it('ignores a worktree another host owns', () => {
    const merged = makeWorktree({ id: 'cobalt:acme-bravo', nato: 'bravo' });
    const agent = agentIn({ worktreeId: merged.id });
    const world = worldWith({ worktrees: [merged], agents: [agent] });
    expect(usedByParent(world, merged, '')).toBe('');
  });
});
