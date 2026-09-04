import { describe, expect, it } from 'vitest';
import { deriveGraph } from './graph';
import { noFilters } from './filters';
import {
  makeAgent,
  makeAttention,
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
  it('a task without an agent is a queued node', () => {
    const world = drawnWorld({ tasks: [makeTask({ id: 'T1', status: 'todo' })] });
    const graph = deriveGraph(world, byProject);
    expect(graph.nodes).toHaveLength(1);
    expect(graph.nodes[0]).toMatchObject({ id: 'T1', state: 'queued', phase: 'executing' });
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
      deriveGraph(world, byProject).nodes.map((n) => [n.id, n.state]),
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
      state: 'needs-attention',
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
