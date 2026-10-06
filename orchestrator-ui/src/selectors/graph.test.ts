import { describe, expect, it } from 'vitest';
import { deriveGraph } from './graph';
import { noFilters } from './filters';
import { deskIdForAgent } from '../protocol/deskId';
import {
  makeAgent,
  makeAttention,
  makeDeskEntry,
  makeTask,
  makeWorktree,
  onDesk,
  worldWith,
} from '../fake/fixtures';

/** A world whose every task is on the desk: what the canvas draws. */
function drawnWorld(parts: Parameters<typeof worldWith>[0]) {
  return worldWith({ ...parts, desk: parts.desk ?? onDesk(parts.tasks ?? []) });
}

const unfiltered = { filters: noFilters() };

describe('deriveGraph', () => {
  it('filters Desk tasks by their agent status', () => {
    const tasks = ['working', 'delegating', 'idle', 'awaiting', 'terminated', 'planned'].map((id) =>
      makeTask({ id }),
    );
    const world = drawnWorld({
      tasks,
      agents: [
        makeAgent({ id: 'working-agent', taskId: 'working', state: 'processing' }),
        makeAgent({ id: 'delegating-agent', taskId: 'delegating', state: 'delegating' }),
        makeAgent({ id: 'idle-agent', taskId: 'idle', state: 'idle' }),
        makeAgent({ id: 'awaiting-agent', taskId: 'awaiting', state: 'awaiting-question' }),
        makeAgent({ id: 'terminated-agent', taskId: 'terminated', state: 'exited', exitCode: 0 }),
      ],
    });
    const shown = (agentStatus: Parameters<typeof deriveGraph>[1]['filters']['agentStatus']) =>
      deriveGraph(world, { ...unfiltered, filters: { ...noFilters(), agentStatus } }).nodes.map(
        (node) => node.id,
      );

    expect(shown('all')).toEqual([
      'awaiting',
      'delegating',
      'idle',
      'planned',
      'terminated',
      'working',
    ]);
    expect(shown('working')).toEqual(['delegating', 'working']);
    expect(shown('idle')).toEqual(['awaiting', 'idle']);
    expect(shown('working-idle')).toEqual(['awaiting', 'delegating', 'idle', 'working']);
    expect(shown('terminated')).toEqual(['terminated']);
    expect(shown('planned')).toEqual(['planned']);
  });

  it('filters free agents by their status, but never treats one as planned', () => {
    const agents = [
      makeAgent({ id: 'working-agent', taskId: '', state: 'processing' }),
      makeAgent({ id: 'idle-agent', taskId: '', state: 'idle' }),
      makeAgent({ id: 'awaiting-agent', taskId: '', state: 'awaiting-question' }),
      makeAgent({ id: 'terminated-agent', taskId: '', state: 'exited', exitCode: 0 }),
    ];
    const world = drawnWorld({
      agents,
      desk: agents.map((agent) => makeDeskEntry({ id: deskIdForAgent(agent.id) })),
    });
    const shown = (agentStatus: Parameters<typeof deriveGraph>[1]['filters']['agentStatus']) =>
      deriveGraph(world, { ...unfiltered, filters: { ...noFilters(), agentStatus } }).nodes.map(
        (node) => node.id,
      );

    expect(shown('all')).toEqual([
      'working-agent',
      'idle-agent',
      'awaiting-agent',
      'terminated-agent',
    ]);
    expect(shown('working')).toEqual(['working-agent']);
    expect(shown('idle')).toEqual(['idle-agent', 'awaiting-agent']);
    expect(shown('working-idle')).toEqual(['working-agent', 'idle-agent', 'awaiting-agent']);
    expect(shown('terminated')).toEqual(['terminated-agent']);
    expect(shown('planned')).toEqual([]);
  });

  it('a task without an agent waits: ready when its turn has come, else queued', () => {
    const ready = drawnWorld({ tasks: [makeTask({ id: 'T1', status: 'todo', actionable: true })] });
    expect(deriveGraph(ready, unfiltered).nodes[0]).toMatchObject({
      id: 'T1',
      progress: expect.objectContaining({ state: 'ready' }),
      phase: 'build',
    });
    const queued = drawnWorld({
      tasks: [makeTask({ id: 'T1', status: 'todo', actionable: false })],
    });
    expect(deriveGraph(queued, unfiltered).nodes[0]).toMatchObject({
      id: 'T1',
      progress: expect.objectContaining({ state: 'queued' }),
    });
  });

  it('draws a plan-mode task as plan until its agent leaves plan mode', () => {
    const tasks = [makeTask({ id: 'T1', command: '', mode: 'plan' })];
    const phaseWith = (permissionMode: 'plan' | 'auto') =>
      deriveGraph(
        drawnWorld({ tasks, agents: [makeAgent({ taskId: 'T1', permissionMode })] }),
        unfiltered,
      ).nodes[0]?.phase;
    expect(phaseWith('plan')).toBe('plan');
    expect(phaseWith('auto')).toBe('build');
  });

  // A running agent works in one worktree, and that is how two runs are told
  // apart on a board of many.
  describe('worktree', () => {
    it('names the worktree an agent runs in', () => {
      const world = drawnWorld({
        tasks: [makeTask({ id: 'T1' })],
        agents: [makeAgent({ taskId: 'T1', worktreeId: 'northwind-golf' })],
        worktrees: [makeWorktree({ id: 'northwind-golf', nato: 'golf' })],
      });
      expect(deriveGraph(world, unfiltered).nodes[0]?.worktree?.nato).toBe('golf');
    });

    it('falls back to the open worktree on the task branch when the agent has stopped', () => {
      const world = drawnWorld({
        tasks: [makeTask({ id: 'T1', branch: 'feat/orders' })],
        worktrees: [makeWorktree({ id: 'northwind-golf', nato: 'golf', branch: 'feat/orders' })],
      });
      expect(deriveGraph(world, unfiltered).nodes[0]?.worktree?.nato).toBe('golf');
    });

    it('prefers the live agent worktree over the branch index', () => {
      const world = drawnWorld({
        tasks: [makeTask({ id: 'T1', branch: 'feat/orders' })],
        agents: [makeAgent({ taskId: 'T1', worktreeId: 'northwind-hotel' })],
        worktrees: [
          makeWorktree({ id: 'northwind-golf', nato: 'golf', branch: 'feat/orders' }),
          makeWorktree({ id: 'northwind-hotel', nato: 'hotel', branch: 'feat/other' }),
        ],
      });
      expect(deriveGraph(world, unfiltered).nodes[0]?.worktree?.nato).toBe('hotel');
    });

    it('has none when no open worktree holds the task branch', () => {
      const world = drawnWorld({ tasks: [makeTask({ id: 'T1', branch: 'feat/nowhere' })] });
      expect(deriveGraph(world, unfiltered).nodes[0]?.worktree).toBeUndefined();
    });
  });

  it('follows becomes an edge from the followed task to the follower', () => {
    const world = drawnWorld({
      tasks: [makeTask({ id: 'T1' }), makeTask({ id: 'T2', follows: ['T1'] })],
    });
    const graph = deriveGraph(world, unfiltered);
    expect(graph.edges).toEqual([{ id: 'T1->T2', source: 'T1', target: 'T2' }]);
  });

  it('a done task is a done node, and a working agent makes a working node', () => {
    const world = drawnWorld({
      tasks: [
        makeTask({ id: 'T1', status: 'done' }),
        makeTask({ id: 'T2', status: 'in-progress' }),
      ],
      agents: [makeAgent({ id: 'a2', taskId: 'T2', state: 'processing' })],
    });
    const states = Object.fromEntries(
      deriveGraph(world, unfiltered).nodes.map((n) => [n.id, n.progress.state]),
    );
    expect(states).toEqual({ T1: 'done', T2: 'working' });
  });

  it('an open attention item makes the node need attention and gives it a reason', () => {
    const world = drawnWorld({
      tasks: [makeTask({ id: 'T2', status: 'in-progress' })],
      agents: [makeAgent({ id: 'a2', taskId: 'T2', state: 'awaiting-question' })],
      attention: [makeAttention({ agentId: 'a2', taskId: 'T2', summary: 'Which colour?' })],
    });
    expect(deriveGraph(world, unfiltered).nodes[0]).toMatchObject({
      progress: expect.objectContaining({ state: 'needs-attention' }),
      reason: 'Which colour?',
    });
  });

  it('groups by project with one group per project', () => {
    const world = drawnWorld({
      tasks: [
        makeTask({ id: 'T1', project: 'northwind' }),
        makeTask({ id: 'T2', project: 'maelstrom' }),
        makeTask({ id: 'T3', project: 'maelstrom' }),
      ],
    });
    const graph = deriveGraph(world, unfiltered);
    expect(graph.groups.map((g) => [g.id, g.nodeIds])).toEqual([
      ['maelstrom', ['T2', 'T3']],
      ['northwind', ['T1']],
    ]);
  });

  describe('empty worktrees of a project lane', () => {
    const worktrees = [
      makeWorktree({ id: 'northwind-charlie', nato: 'charlie', branch: 'feat/idle-2' }),
      makeWorktree({ id: 'northwind-alpha', nato: 'alpha', branch: 'feat/orders' }),
      makeWorktree({ id: 'northwind-bravo', nato: 'bravo', branch: 'feat/idle' }),
      makeWorktree({ id: '_main', nato: '_main', branch: 'main' }),
      makeWorktree({ id: 'northwind-delta', nato: 'delta', branch: '', isClosed: true }),
      makeWorktree({ id: 'maelstrom-alpha', project: 'maelstrom', nato: 'alpha', branch: 'x' }),
    ];
    const tasks = [makeTask({ id: 'T1', project: 'northwind', branch: 'feat/orders' })];

    // Open, of this project, holding no node. Not `_main`, not the closed one,
    // not the other project's, and not alpha, which T1 is in.
    it('lists the open worktrees of the project that hold no node, in name order', () => {
      const graph = deriveGraph(drawnWorld({ worktrees, tasks }), unfiltered);
      expect(
        graph.groups.find((g) => g.id === 'northwind')?.emptyWorktrees.map((w) => w.id),
      ).toEqual(['northwind-bravo', 'northwind-charlie']);
    });

    it('lists a worktree no longer once a free agent runs in it', () => {
      const agent = makeAgent({ id: 'A1', taskId: '', worktreeId: 'northwind-bravo' });
      const graph = deriveGraph(drawnWorld({ worktrees, tasks, agents: [agent] }), unfiltered);
      expect(
        graph.groups.find((g) => g.id === 'northwind')?.emptyWorktrees.map((w) => w.id),
      ).toEqual(['northwind-charlie']);
    });

    // A filter hides nodes, so "no node drawn" stops meaning "holds nothing".
    it.each([
      { ...noFilters(), agentStatus: 'working' as const },
      { ...noFilters(), branch: 'northwind/feat/orders' },
    ])('lists none while a filter hides nodes: %o', (filters) => {
      const agent = makeAgent({ id: 'A1', taskId: 'T1', state: 'processing' });
      const graph = deriveGraph(drawnWorld({ worktrees, tasks, agents: [agent] }), {
        filters,
      });
      expect(graph.groups.map((g) => [g.nodeIds, g.emptyWorktrees])).toEqual([[['T1'], []]]);
    });

    // The lane comes from the world here, not from a node: without it the
    // empty boxes of a project with no work on the desk would never draw.
    it('draws a lane for a project that holds only empty worktrees', () => {
      const graph = deriveGraph(drawnWorld({ worktrees, tasks }), unfiltered);
      expect(graph.groups.map((g) => [g.id, g.nodeIds, g.emptyWorktrees.map((w) => w.id)])).toEqual(
        [
          ['maelstrom', [], ['maelstrom-alpha']],
          ['northwind', ['T1'], ['northwind-bravo', 'northwind-charlie']],
        ],
      );
    });

    it('draws no such lane for a project the filter leaves out', () => {
      const graph = deriveGraph(drawnWorld({ worktrees, tasks }), {
        ...unfiltered,
        filters: { ...noFilters(), project: 'northwind' },
      });
      expect(graph.groups.map((g) => g.id)).toEqual(['northwind']);
    });

    it('draws no lane for a project whose only open worktree is `_main`', () => {
      const main = makeWorktree({ id: 'maelstrom-_main', project: 'maelstrom', nato: '_main' });
      const graph = deriveGraph(drawnWorld({ worktrees: [main], tasks }), unfiltered);
      expect(graph.groups.map((g) => g.id)).toEqual(['northwind']);
    });
  });

  it('filters drop nodes and the edges that dangle from them', () => {
    const world = drawnWorld({
      tasks: [
        makeTask({ id: 'T1', branch: 'feat/orders' }),
        makeTask({ id: 'T2', branch: 'feat/db', follows: ['T1'] }),
        makeTask({ id: 'T3', branch: 'feat/db', follows: ['T2'] }),
      ],
    });
    const graph = deriveGraph(world, {
      filters: { ...noFilters(), branch: 'northwind/feat/db' },
    });
    expect(graph.nodes.map((n) => n.id)).toEqual(['T2', 'T3']);
    expect(graph.edges.map((e) => e.id)).toEqual(['T2->T3']);
  });

  it('a task off the desk with no live agent is not drawn', () => {
    const drawn = makeTask({ id: 'T3', status: 'todo' });
    const world = worldWith({
      tasks: [makeTask({ id: 'T1' }), makeTask({ id: 'T2' }), drawn],
      desk: onDesk([drawn]),
      agents: [makeAgent({ id: 'a1', taskId: 'T1', state: 'exited', exitCode: 0 })],
    });
    const graph = deriveGraph(world, unfiltered);
    expect(graph.nodes.map((n) => n.id)).toEqual(['T3']);
  });

  it('a task off the desk is drawn while its agent is live', () => {
    const world = worldWith({
      tasks: [makeTask({ id: 'T1' }), makeTask({ id: 'T2' })],
      desk: [],
      agents: [makeAgent({ id: 'a1', taskId: 'T1', state: 'processing' })],
    });
    const graph = deriveGraph(world, unfiltered);
    expect(graph.nodes.map((n) => n.id)).toEqual(['T1']);
  });

  it('the project filter keeps only that project and its group', () => {
    const world = drawnWorld({
      tasks: [
        makeTask({ id: 'T1', project: 'northwind' }),
        makeTask({ id: 'T2', project: 'maelstrom' }),
      ],
    });
    const graph = deriveGraph(world, {
      filters: { ...noFilters(), project: 'maelstrom' },
    });
    expect(graph.nodes.map((n) => n.id)).toEqual(['T2']);
    expect(graph.groups.map((g) => g.id)).toEqual(['maelstrom']);
  });
});

describe('free agents', () => {
  const freeAgent = (over = {}) =>
    makeAgent({
      id: 'free1',
      taskId: '',
      worktreeId: 'northwind-alpha',
      state: 'processing',
      ...over,
    });

  it('a live agent with no task is a freeAgent node', () => {
    const world = worldWith({
      worktrees: [makeWorktree({ id: 'northwind-alpha', branch: 'feat/orders' })],
      agents: [freeAgent()],
      desk: [],
    });
    const graph = deriveGraph(world, unfiltered);
    expect(graph.nodes).toHaveLength(1);
    expect(graph.nodes[0]).toMatchObject({
      id: 'free1',
      kind: 'freeAgent',
      task: undefined,
      progress: expect.objectContaining({ state: 'working' }),
    });
    expect(graph.nodes[0]?.worktree?.nato).toBe('alpha');
  });

  it('an agent with a task draws as its task node, not a second node', () => {
    const world = worldWith({
      tasks: [makeTask({ id: 'T1', status: 'in-progress' })],
      agents: [makeAgent({ id: 'a1', taskId: 'T1', state: 'processing' })],
      desk: [],
    });
    const graph = deriveGraph(world, unfiltered);
    expect(graph.nodes.map((n) => [n.id, n.kind])).toEqual([['T1', 'task']]);
  });

  it('an exited free agent is drawn only while it is on the desk', () => {
    const exited = freeAgent({ state: 'exited', exitCode: 0 });
    const off = worldWith({ agents: [exited], desk: [] });
    expect(deriveGraph(off, unfiltered).nodes).toHaveLength(0);

    const on = worldWith({
      agents: [exited],
      desk: [makeDeskEntry({ id: deskIdForAgent('free1') })],
    });
    expect(deriveGraph(on, unfiltered).nodes.map((n) => n.id)).toEqual(['free1']);
  });

  it('a free agent takes its lane from its worktree, and one with neither takes the unnamed lane', () => {
    const world = worldWith({
      worktrees: [makeWorktree({ id: 'maelstrom-alpha', project: 'maelstrom', nato: 'alpha' })],
      agents: [
        freeAgent({ id: 'held', project: '', worktreeId: 'maelstrom-alpha' }),
        freeAgent({ id: 'lost', project: '', worktreeId: 'unread' }),
      ],
      desk: [],
    });
    const graph = deriveGraph(world, unfiltered);
    expect(graph.groups.map((g) => [g.id, g.nodeIds])).toEqual([
      ['', ['lost']],
      ['maelstrom', ['held']],
    ]);
  });

  it('the filters apply to a free agent, by the project and branch it runs in', () => {
    const world = worldWith({
      worktrees: [makeWorktree({ id: 'northwind-alpha', branch: 'feat/orders' })],
      agents: [freeAgent()],
      desk: [],
    });
    const kept = { filters: { project: 'northwind', branch: null } };
    expect(deriveGraph(world, kept).nodes.map((n) => n.id)).toEqual(['free1']);

    const otherProject = {
      filters: { project: 'maelstrom', branch: null },
    };
    expect(deriveGraph(world, otherProject).nodes).toHaveLength(0);

    const otherBranch = {
      filters: { project: null, branch: 'northwind/feat/other' },
    };
    expect(deriveGraph(world, otherBranch).nodes).toHaveLength(0);
  });

  it("a subagent is neither its task node's agent nor a free-agent node", () => {
    const world = worldWith({
      worktrees: [makeWorktree({ id: 'northwind-alpha', branch: 'feat/orders' })],
      tasks: [makeTask({ id: 'northwind/NORT-7' })],
      agents: [
        makeAgent({ id: 'p1', taskId: 'northwind/NORT-7', state: 'idle' }),
        makeAgent({ id: 'p1.1', parent: 'p1', description: 'Scan', taskId: 'northwind/NORT-7' }),
        freeAgent(),
        makeAgent({ id: 'free1.1', parent: 'free1', description: 'Scan', taskId: '' }),
      ],
      desk: onDesk([makeTask({ id: 'northwind/NORT-7' })]),
    });
    const graph = deriveGraph(world, unfiltered);
    expect(graph.nodes.map((n) => n.id).sort()).toEqual(['free1', 'northwind/NORT-7']);
    expect(graph.nodes.find((n) => n.id === 'northwind/NORT-7')?.agent?.id).toBe('p1');
  });

  it('edges stay task-only: a free agent is never an endpoint', () => {
    const world = worldWith({
      tasks: [makeTask({ id: 'T1' }), makeTask({ id: 'T2', follows: ['T1'] })],
      agents: [freeAgent()],
      desk: onDesk([makeTask({ id: 'T1' }), makeTask({ id: 'T2' })]),
    });
    const graph = deriveGraph(world, unfiltered);
    expect(graph.edges).toEqual([{ id: 'T1->T2', source: 'T1', target: 'T2' }]);
  });
});
