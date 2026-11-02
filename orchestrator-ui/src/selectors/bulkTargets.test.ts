import { describe, expect, it } from 'vitest';
import { makeAgent, makeDeskEntry, makeTask, makeWorktree, worldWith } from '../fake/fixtures';
import type { Agent, Task, Worktree } from '../protocol/entities';
import { deriveGraph, type GraphNode } from './graph';
import { noFilters } from './filters';
import { linkedComms, planDismiss } from './bulkTargets';
import type { WorldView } from './world';

const alpha = makeWorktree({ id: 'northwind-alpha', nato: 'alpha' });
const main = makeWorktree({ id: 'northwind-main', nato: '_main', branch: 'main' });

/** A world with every task and free agent on the desk, and its drawn nodes by id. */
function board(parts: { tasks?: Task[]; agents?: Agent[]; worktrees?: Worktree[] }) {
  const world = worldWith({
    worktrees: [alpha, main],
    ...parts,
    desk: [
      ...(parts.tasks ?? []).map((t) => makeDeskEntry({ id: `task:${t.id}` })),
      ...(parts.agents ?? [])
        .filter((a) => !a.taskId)
        .map((a) => makeDeskEntry({ id: `agent:${a.id}` })),
    ],
  });
  const nodes = Object.fromEntries(
    deriveGraph(world, { filters: noFilters() }).nodes.map((n) => [n.id, n]),
  );
  return { world: world as WorldView, nodes };
}

const free = makeAgent({ id: 'free-1', taskId: '', worktreeId: 'northwind-alpha' });

describe('linkedComms', () => {
  it('offers to unlink each comm any selected task is linked to, once', () => {
    const { nodes } = board({
      tasks: [makeTask({ id: 'T1', comms: ['c1', 'c2'] }), makeTask({ id: 'T2', comms: ['c2'] })],
    });
    expect(linkedComms([nodes.T1!, nodes.T2!])).toEqual(['c1', 'c2']);
  });
});

describe('planDismiss', () => {
  /** Each step as `id chain`, the worktree after a close. */
  const plan = (world: WorldView, nodes: GraphNode[]) =>
    planDismiss(world, nodes).map((s) =>
      [s.node.id, s.chain, s.worktreeId].filter(Boolean).join(' '),
    );

  it('terminates a live agent, and takes a node that is not live off the desk', () => {
    const { world, nodes } = board({
      // No worktree is on T2's branch, and `_main` never closes.
      tasks: [makeTask({ id: 'T1' }), makeTask({ id: 'T2', branch: 'feat/none' })],
      agents: [makeAgent({ id: 'a1', taskId: 'T1', worktreeId: 'northwind-main' })],
    });
    expect(plan(world, [nodes.T1!, nodes.T2!])).toEqual(['T1 terminate', 'T2 offDesk']);
  });

  it('skips a node with nothing live and no desk entry', () => {
    const { world, nodes } = board({ tasks: [makeTask({ id: 'T1', branch: 'feat/none' })] });
    // Taken off the desk after the node drew: a node with neither does not draw.
    delete world.desk['task:T1'];
    expect(plan(world, [nodes.T1!])).toEqual([]);
  });

  it('closes a closable worktree once, on the last selected node in it', () => {
    const { world, nodes } = board({
      tasks: [makeTask({ id: 'T1' }), makeTask({ id: 'T2' })],
      agents: [
        makeAgent({ id: 'a1', taskId: 'T1', worktreeId: 'northwind-alpha' }),
        makeAgent({ id: 'a2', taskId: 'T2', worktreeId: 'northwind-alpha' }),
      ],
    });
    expect(plan(world, [nodes.T1!, nodes.T2!])).toEqual([
      'T1 terminate',
      'T2 close northwind-alpha',
    ]);
  });

  it('closes nothing while an unselected agent still runs in the worktree', () => {
    const { world, nodes } = board({
      tasks: [makeTask({ id: 'T1' })],
      agents: [
        makeAgent({
          id: 'a1',
          taskId: 'T1',
          worktreeId: 'northwind-alpha',
          state: 'exited',
          exitCode: 0,
        }),
        free,
      ],
    });
    expect(plan(world, [nodes.T1!])).toEqual(['T1 offDesk']);
    expect(plan(world, [nodes.T1!, nodes['free-1']!])).toEqual([
      'T1 offDesk',
      'free-1 close northwind-alpha',
    ]);
  });
});
