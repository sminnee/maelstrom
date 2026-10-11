import { describe, expect, it } from 'vitest';

import type { Operation, OperationStep } from '../protocol/entities';
import { boxWords, unitOperation, unitWords, type Unit } from './operations';

const step = (name: string, state: OperationStep['state'], words = ''): OperationStep => ({
  name,
  words,
  state,
  startedAt: state === 'pending' ? null : '2026-10-12T10:00:00Z',
  endedAt: state === 'pending' || state === 'running' ? null : '2026-10-12T10:00:05Z',
});

function op(o: Partial<Operation> & Pick<Operation, 'id'>): Operation {
  return {
    kind: 'close',
    worktreeId: 'acme-alpha',
    taskId: null,
    agentId: null,
    words: 'Closing alpha',
    state: 'running',
    startedAt: '2026-10-12T10:00:00Z',
    endedAt: null,
    seen: false,
    steps: [step('stop_env', 'running')],
    ...o,
  };
}

/** The pick must not depend on the order the server lists the operations in. */
function bothOrders(ops: Operation[], unit: Unit): [Operation | undefined, Operation | undefined] {
  return [unitOperation(ops, unit), unitOperation([...ops].reverse(), unit)];
}

const box: Unit = { worktreeId: 'acme-alpha' };
const node: Unit = { node: { worktreeId: 'acme-alpha', taskId: 'acme/1', agentId: 'ag1' } };

describe('unitOperation', () => {
  it('picks the running operation over an unseen one', () => {
    const ops = [
      op({ id: 'op1', state: 'refused', startedAt: '2026-10-12T11:00:00Z' }),
      op({ id: 'op2', state: 'running', startedAt: '2026-10-12T10:00:00Z' }),
    ];
    expect(bothOrders(ops, box).map((o) => o?.id)).toEqual(['op2', 'op2']);
  });

  it('picks the newest unseen operation', () => {
    const ops = [
      op({ id: 'op1', state: 'refused', startedAt: '2026-10-12T10:00:00Z' }),
      op({ id: 'op2', state: 'failed', startedAt: '2026-10-12T11:00:00Z' }),
    ];
    expect(bothOrders(ops, box).map((o) => o?.id)).toEqual(['op2', 'op2']);
  });

  it('leaves the unit clean after a seen bad end or a done operation', () => {
    const ops = [
      op({ id: 'op1', state: 'refused', seen: true }),
      op({ id: 'op2', state: 'done', seen: true }),
    ];
    expect(bothOrders(ops, box)).toEqual([undefined, undefined]);
    expect(bothOrders(ops, node)).toEqual([undefined, undefined]);
  });

  it('marks a node for an ending kind on its worktree, but not for a sync', () => {
    const ops = [
      op({ id: 'op1', kind: 'sync', startedAt: '2026-10-12T11:00:00Z' }),
      op({ id: 'op2', kind: 'shelve', state: 'failed', startedAt: '2026-10-12T10:00:00Z' }),
    ];
    expect(bothOrders(ops, node).map((o) => o?.id)).toEqual(['op2', 'op2']);
    expect(bothOrders([ops[0]!], node)).toEqual([undefined, undefined]);
  });

  it('marks a node for any kind that names its task or its agent', () => {
    const byTask = op({ id: 'op1', kind: 'sync', worktreeId: 'acme-bravo', taskId: 'acme/1' });
    const byAgent = op({ id: 'op2', kind: 'env', worktreeId: 'acme-bravo', agentId: 'ag1' });
    expect(unitOperation([byTask], node)?.id).toBe('op1');
    expect(unitOperation([byAgent], node)?.id).toBe('op2');
    const other: Unit = { node: { worktreeId: 'acme-alpha', taskId: 'acme/2', agentId: null } };
    expect(unitOperation([byTask, byAgent], other)).toBeUndefined();
  });

  it('marks a worktree for any kind on it, and not for another worktree', () => {
    for (const kind of ['close', 'sync', 'merge', 'env'] as const) {
      expect(unitOperation([op({ id: 'op1', kind })], box)?.id).toBe('op1');
    }
    expect(unitOperation([op({ id: 'op1', worktreeId: 'acme-bravo' })], box)).toBeUndefined();
  });

  it('marks no node that has no worktree, task or agent', () => {
    const bare: Unit = { node: { worktreeId: null, taskId: null, agentId: null } };
    expect(unitOperation([op({ id: 'op1' })], bare)).toBeUndefined();
  });
});

// The words of a running close and a refused close are read through the UI in
// App.operationsBoard.test.tsx. These are the cases the scenario does not reach.
describe('unitWords', () => {
  it('holds at the last step once every step is done but the operation still runs', () => {
    const ending = op({ id: 'op1', steps: [step('stop_env', 'done'), step('git_close', 'done')] });
    expect(unitWords(ending)).toBe('Closing alpha · 2/2');
  });
});

describe('boxWords', () => {
  it('reads an operation that ended badly as its kind and how it ended', () => {
    expect(boxWords(op({ id: 'op1', kind: 'shelve', state: 'failed' }))).toBe('shelve failed');
  });

  it('reads an environment run as env while it runs', () => {
    expect(boxWords(op({ id: 'op1', kind: 'env' }))).toBe('env 1/1');
  });
});
