import { describe, expect, it } from 'vitest';
import type { Task } from '../protocol/entities';
import type { TaskRow } from '../api/types';
import { makeTask } from '../test/fixtures';
import { followsReach } from './follows';

const byId = (tasks: Task[]) => Object.fromEntries(tasks.map((t) => [t.id, t]));
const ids = (tasks: TaskRow[]) => tasks.map((t) => t.id);

describe('followsReach', () => {
  it('lists what a task follows and what follows it, direct and indirect, nearest first', () => {
    const tasks = byId([
      makeTask({ id: 'A' }),
      makeTask({ id: 'B', follows: ['A'] }),
      makeTask({ id: 'C', follows: ['B'] }),
      makeTask({ id: 'D', follows: ['C'] }),
    ]);
    expect(ids(followsReach(tasks, 'B').before)).toEqual(['A']);
    expect(ids(followsReach(tasks, 'B').after)).toEqual(['C', 'D']);
    expect(ids(followsReach(tasks, 'D').before)).toEqual(['C', 'B', 'A']);
    expect(ids(followsReach(tasks, 'A').before)).toEqual([]);
  });

  it('reports a diamond once, and one depth in id order whatever the input order', () => {
    const shapes = [
      [
        makeTask({ id: 'A' }),
        makeTask({ id: 'C', follows: ['A'] }),
        makeTask({ id: 'B', follows: ['A'] }),
        makeTask({ id: 'D', follows: ['C', 'B'] }),
      ],
      [
        makeTask({ id: 'D', follows: ['B', 'C'] }),
        makeTask({ id: 'B', follows: ['A'] }),
        makeTask({ id: 'C', follows: ['A'] }),
        makeTask({ id: 'A' }),
      ],
    ];
    for (const shape of shapes) {
      const tasks = byId(shape);
      expect(ids(followsReach(tasks, 'A').after)).toEqual(['B', 'C', 'D']);
      expect(ids(followsReach(tasks, 'D').before)).toEqual(['B', 'C', 'A']);
    }
  });

  it('ends on a cycle and never lists the task itself', () => {
    const tasks = byId([
      makeTask({ id: 'A', follows: ['B'] }),
      makeTask({ id: 'B', follows: ['A'] }),
    ]);
    expect(ids(followsReach(tasks, 'A').before)).toEqual(['B']);
    expect(ids(followsReach(tasks, 'A').after)).toEqual(['B']);
  });

  it('skips an id with no task', () => {
    const tasks = byId([makeTask({ id: 'B', follows: ['gone'] })]);
    expect(followsReach(tasks, 'B').before).toEqual([]);
  });
  it('lists a task reachable at two depths once, at the nearer', () => {
    const tasks = byId([
      makeTask({ id: 'A' }),
      makeTask({ id: 'B', follows: ['A'] }),
      makeTask({ id: 'C', follows: ['B'] }),
      makeTask({ id: 'D', follows: ['C', 'A'] }),
    ]);
    expect(ids(followsReach(tasks, 'D').before)).toEqual(['A', 'C', 'B']);
  });

  it('orders one depth by the number in the id, not its characters', () => {
    const tasks = byId([
      makeTask({ id: 'MAEL-52' }),
      makeTask({ id: 'MAEL-52.10', follows: ['MAEL-52'] }),
      makeTask({ id: 'MAEL-52.9', follows: ['MAEL-52'] }),
    ]);
    expect(ids(followsReach(tasks, 'MAEL-52').after)).toEqual(['MAEL-52.9', 'MAEL-52.10']);
  });
});
