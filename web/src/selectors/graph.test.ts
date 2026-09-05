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
} from '../test/fixtures';

/** A world whose every task is on the desk: what the canvas draws. */
function drawnWorld(parts: Parameters<typeof worldWith>[0]) {
  return worldWith({ ...parts, desk: parts.desk ?? onDesk(parts.tasks ?? []) });
}

const byProject = { groupBy: 'project' as const, filters: noFilters() };

describe('deriveGraph', () => {
  it('a task without an agent waits: ready when its turn has come, else queued', () => {
    const ready = drawnWorld({ tasks: [makeTask({ id: 'T1', status: 'todo', actionable: true })] });
    expect(deriveGraph(ready, byProject).nodes[0]).toMatchObject({
      id: 'T1',
      progress: expect.objectContaining({ state: 'ready' }),
      phase: 'build',
    });
    const queued = drawnWorld({
      tasks: [makeTask({ id: 'T1', status: 'todo', actionable: false })],
    });
    expect(deriveGraph(queued, byProject).nodes[0]).toMatchObject({
      id: 'T1',
      progress: expect.objectContaining({ state: 'queued' }),
    });
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
      expect(deriveGraph(world, byProject).nodes[0]?.worktree?.nato).toBe('golf');
    });

    it('has none when no agent runs the task', () => {
      const world = drawnWorld({ tasks: [makeTask({ id: 'T1' })] });
      expect(deriveGraph(world, byProject).nodes[0]?.worktree).toBeUndefined();
    });
  });

  // The node says the project only when nothing else on screen already does:
  // the lane header names it when grouped by project, and the filter names it
  // when filtered to one.
  describe('showProject', () => {
    const world = drawnWorld({
      tasks: [makeTask({ id: 'northwind/NORT-7', project: 'northwind' })],
    });
    const showProject = (opts: Parameters<typeof deriveGraph>[1]) =>
      deriveGraph(world, opts).nodes[0]!.showProject;

    it('is false when the lane header names the project', () => {
      expect(showProject(byProject)).toBe(false);
    });

    it('is false when the project filter names the project', () => {
      const filters = { ...noFilters(), project: 'northwind' };
      expect(showProject({ groupBy: 'none', filters })).toBe(false);
    });

    it('is true when neither the lane nor the filter names it', () => {
      expect(showProject({ groupBy: 'none', filters: noFilters() })).toBe(true);
      expect(showProject({ groupBy: 'branch', filters: noFilters() })).toBe(true);
    });
  });

  it('follows becomes an edge from the followed task to the follower', () => {
    const world = drawnWorld({
      tasks: [makeTask({ id: 'T1' }), makeTask({ id: 'T2', follows: ['T1'] })],
    });
    const graph = deriveGraph(world, byProject);
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
      deriveGraph(world, byProject).nodes.map((n) => [n.id, n.progress.state]),
    );
    expect(states).toEqual({ T1: 'done', T2: 'working' });
  });

  it('an open attention item makes the node need attention and gives it a reason', () => {
    const world = drawnWorld({
      tasks: [makeTask({ id: 'T2', status: 'in-progress' })],
      agents: [makeAgent({ id: 'a2', taskId: 'T2', state: 'awaiting-question' })],
      attention: [makeAttention({ agentId: 'a2', taskId: 'T2', summary: 'Which colour?' })],
    });
    expect(deriveGraph(world, byProject).nodes[0]).toMatchObject({
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
    const graph = deriveGraph(world, byProject);
    expect(graph.groups.map((g) => [g.id, g.nodeIds])).toEqual([
      ['maelstrom', ['T2', 'T3']],
      ['northwind', ['T1']],
    ]);
  });

  it('groups by branch with one group per branch, labelled with its worktree', () => {
    const world = drawnWorld({
      worktrees: [makeWorktree({ id: 'northwind-bravo', nato: 'bravo', branch: 'feat/db' })],
      tasks: [
        makeTask({ id: 'T1', branch: 'feat/orders' }),
        makeTask({ id: 'T2', branch: 'feat/db' }),
        makeTask({ id: 'T3', branch: 'feat/db' }),
      ],
    });
    const graph = deriveGraph(world, { groupBy: 'branch', filters: noFilters() });
    const db = graph.groups.find((g) => g.label === 'feat/db');
    expect(db).toMatchObject({ sublabel: 'bravo', nodeIds: ['T2', 'T3'] });
    expect(graph.groups.find((g) => g.label === 'feat/orders')?.sublabel).toBe('');
    expect(graph.nodes.find((n) => n.id === 'T2')?.groupId).toBe(db?.id);
  });

  it('groups by none with one unlabelled group holding every node, edges kept', () => {
    const world = drawnWorld({
      tasks: [
        makeTask({ id: 'T1', project: 'northwind' }),
        makeTask({ id: 'T2', project: 'maelstrom', follows: ['T1'] }),
      ],
    });
    const graph = deriveGraph(world, { groupBy: 'none', filters: noFilters() });
    expect(graph.groups).toEqual([
      { id: 'all', kind: 'none', label: '', sublabel: '', nodeIds: ['T1', 'T2'] },
    ]);
    expect(graph.nodes.map((n) => n.groupId)).toEqual(['all', 'all']);
    expect(graph.edges.map((e) => e.id)).toEqual(['T1->T2']);
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
      groupBy: 'project',
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
    const graph = deriveGraph(world, byProject);
    expect(graph.nodes.map((n) => n.id)).toEqual(['T3']);
  });

  it('a task off the desk is drawn while its agent is live', () => {
    const world = worldWith({
      tasks: [makeTask({ id: 'T1' }), makeTask({ id: 'T2' })],
      desk: [],
      agents: [makeAgent({ id: 'a1', taskId: 'T1', state: 'processing' })],
    });
    const graph = deriveGraph(world, byProject);
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
      groupBy: 'project',
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
    const graph = deriveGraph(world, byProject);
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
    const graph = deriveGraph(world, byProject);
    expect(graph.nodes.map((n) => [n.id, n.kind])).toEqual([['T1', 'task']]);
  });

  it('an exited free agent is drawn only while it is on the desk', () => {
    const exited = freeAgent({ state: 'exited', exitCode: 0 });
    const off = worldWith({ agents: [exited], desk: [] });
    expect(deriveGraph(off, byProject).nodes).toHaveLength(0);

    const on = worldWith({
      agents: [exited],
      desk: [makeDeskEntry({ id: deskIdForAgent('free1') })],
    });
    expect(deriveGraph(on, byProject).nodes.map((n) => n.id)).toEqual(['free1']);
  });

  it('a free agent takes its branch lane from its worktree', () => {
    const world = worldWith({
      worktrees: [makeWorktree({ id: 'northwind-alpha', branch: 'feat/orders' })],
      agents: [freeAgent()],
      desk: [],
    });
    const graph = deriveGraph(world, { groupBy: 'branch', filters: noFilters() });
    expect(graph.groups.map((g) => [g.id, g.label, g.sublabel])).toEqual([
      ['northwind/feat/orders', 'feat/orders', 'alpha'],
    ]);
  });

  it('a free agent with no worktree falls in the unknown lane', () => {
    const world = worldWith({ agents: [freeAgent({ worktreeId: '' })], desk: [] });
    const graph = deriveGraph(world, { groupBy: 'branch', filters: noFilters() });
    expect(graph.nodes).toHaveLength(1);
    expect(graph.groups[0]?.label).toBe('(no branch)');
  });

  it('the filters apply to a free agent, by the project and branch it runs in', () => {
    const world = worldWith({
      worktrees: [makeWorktree({ id: 'northwind-alpha', branch: 'feat/orders' })],
      agents: [freeAgent()],
      desk: [],
    });
    const kept = { groupBy: 'project' as const, filters: { project: 'northwind', branch: null } };
    expect(deriveGraph(world, kept).nodes.map((n) => n.id)).toEqual(['free1']);

    const otherProject = {
      groupBy: 'project' as const,
      filters: { project: 'maelstrom', branch: null },
    };
    expect(deriveGraph(world, otherProject).nodes).toHaveLength(0);

    const otherBranch = {
      groupBy: 'project' as const,
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
    const graph = deriveGraph(world, byProject);
    expect(graph.nodes.map((n) => n.id).sort()).toEqual(['free1', 'northwind/NORT-7']);
    expect(graph.nodes.find((n) => n.id === 'northwind/NORT-7')?.agent?.id).toBe('p1');
  });

  it('edges stay task-only: a free agent is never an endpoint', () => {
    const world = worldWith({
      tasks: [makeTask({ id: 'T1' }), makeTask({ id: 'T2', follows: ['T1'] })],
      agents: [freeAgent()],
      desk: onDesk([makeTask({ id: 'T1' }), makeTask({ id: 'T2' })]),
    });
    const graph = deriveGraph(world, byProject);
    expect(graph.edges).toEqual([{ id: 'T1->T2', source: 'T1', target: 'T2' }]);
  });
});
