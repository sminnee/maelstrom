import { describe, expect, it } from 'vitest';
import type { AttentionKind } from '../protocol/attention';
import { attentionNodes, nextAttentionNode } from './attention';
import { noFilters } from './filters';
import { deriveGraph } from './graph';
import { makeAgent, makeAttention, makeTask, onDesk, worldWith } from '../test/fixtures';

/** A task on the desk whose turning agent holds one open item. */
function asking(id: string, kind: AttentionKind, second: number) {
  return {
    task: makeTask({ id, status: 'in-progress' }),
    agent: makeAgent({ id: `agent-${id}`, taskId: id }),
    item: makeAttention({
      id: `att-${id}`,
      kind,
      taskId: id,
      agentId: `agent-${id}`,
      raisedAt: `2026-09-01T00:00:0${second}Z`,
    }),
  };
}

/** A task on the desk whose agent ended its turn with a message. */
function spoke(id: string, second: number) {
  return {
    task: makeTask({ id, status: 'in-progress' }),
    agent: makeAgent({
      id: `agent-${id}`,
      taskId: id,
      state: 'idle',
      lastMessage: 'Which default do you want?',
      lastMessageAt: `2026-09-01T00:00:0${second}Z`,
    }),
  };
}

function nodesOf(parts: Parameters<typeof worldWith>[0]) {
  const world = worldWith({ ...parts, desk: onDesk(parts.tasks ?? []) });
  return deriveGraph(world, { filters: noFilters() }).nodes;
}

const ids = (parts: Parameters<typeof worldWith>[0]) =>
  attentionNodes(nodesOf(parts)).map((n) => n.id);

describe('attentionNodes', () => {
  it('orders plan reviews, document reviews, questions, permissions, then the rest, oldest first', () => {
    const cases = [
      asking('T5', 'agent_exited', 0),
      asking('T6', 'document_review', 8),
      asking('T1', 'question', 1),
      asking('T2', 'plan_review', 9),
      asking('T3', 'permission', 2),
      asking('T4', 'plan_review', 3),
    ];
    expect(
      ids({
        tasks: cases.map((c) => c.task),
        agents: cases.map((c) => c.agent),
        attention: cases.map((c) => c.item),
      }),
    ).toEqual(['T4', 'T2', 'T6', 'T1', 'T3', 'T5']);
  });

  it('ranks a node by its best item, not its oldest', () => {
    const early = asking('T1', 'question', 2);
    const late = asking('T2', 'permission', 1);
    const review = makeAttention({
      id: 'att-T2-plan',
      kind: 'plan_review',
      taskId: 'T2',
      agentId: 'agent-T2',
      raisedAt: '2026-09-01T00:00:08Z',
    });
    expect(
      ids({
        tasks: [early.task, late.task],
        agents: [early.agent, late.agent],
        attention: [early.item, late.item, review],
      }),
    ).toEqual(['T2', 'T1']);
  });

  it('leaves out a done task whose item is still open', () => {
    const done = asking('T1', 'question', 1);
    expect(
      ids({
        tasks: [{ ...done.task, status: 'done' }],
        agents: [{ ...done.agent, state: 'exited', exitCode: 0 }],
        attention: [done.item],
      }),
    ).toEqual([]);
  });

  it('leaves out a node whose agent exited nonzero', () => {
    const dead = asking('T1', 'question', 1);
    expect(
      ids({
        tasks: [dead.task],
        agents: [{ ...dead.agent, state: 'exited', exitCode: 1 }],
        attention: [dead.item],
      }),
    ).toEqual([]);
  });

  it('keeps a free agent that waits on the user', () => {
    expect(
      ids({
        agents: [makeAgent({ id: 'free-1', taskId: '' })],
        attention: [makeAttention({ kind: 'question', taskId: null, agentId: 'free-1' })],
      }),
    ).toEqual(['free-1']);
  });
});

describe('nextAttentionNode', () => {
  const cases = [asking('T1', 'question', 1), asking('T2', 'plan_review', 2)];
  const nodes = nodesOf({
    tasks: cases.map((c) => c.task),
    agents: cases.map((c) => c.agent),
    attention: cases.map((c) => c.item),
  });

  it('starts at the top and cycles', () => {
    expect(nextAttentionNode(nodes, null)).toBe('T2');
    expect(nextAttentionNode(nodes, 'T2')).toBe('T1');
    expect(nextAttentionNode(nodes, 'T1')).toBe('T2');
  });

  it('visits the unanswered nodes after the asking ones, then starts again', () => {
    const waiting = [spoke('T8', 7), spoke('T9', 3)];
    const mixed = nodesOf({
      tasks: [...waiting, ...cases].map((c) => c.task),
      agents: [...waiting, ...cases].map((c) => c.agent),
      attention: cases.map((c) => c.item),
    });
    const visited: (string | null)[] = [];
    let at: string | null = null;
    for (let i = 0; i < 5; i += 1) visited.push((at = nextAttentionNode(mixed, at)));
    // T9 spoke before T8, so it leads the unanswered nodes.
    expect(visited).toEqual(['T2', 'T1', 'T9', 'T8', 'T2']);
    expect(attentionNodes(mixed).map((n) => n.id)).toEqual(['T2', 'T1']);
  });

  it('is null when no node needs the user', () => {
    expect(nextAttentionNode(nodesOf({}), null)).toBeNull();
  });
});
