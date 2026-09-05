import { describe, expect, it } from 'vitest';
import { assignColumns, type ColumnInput } from './columns';

/** A node the engine can read: an id, a zone, and what it follows. */
function n(id: string, zone: ColumnInput['zone'], ...follows: string[]): ColumnInput {
  return { id, zone, follows };
}

describe('assignColumns', () => {
  it('gives an empty input no nodes and no width', () => {
    const result = assignColumns([]);
    expect([...result.byId]).toEqual([]);
    expect(result.widths).toEqual({ done: 0, running: 0, notStarted: 0 });
  });

  it('puts one node in the first column of its own zone', () => {
    const result = assignColumns([n('A', 'notStarted')]);
    expect(result.byId.get('A')).toEqual({ zone: 'notStarted', column: 0 });
    expect(result.widths).toEqual({ done: 0, running: 0, notStarted: 1 });
  });

  it('spreads a chain in one zone across columns', () => {
    const result = assignColumns([
      n('A', 'notStarted'),
      n('B', 'notStarted', 'A'),
      n('C', 'notStarted', 'B'),
    ]);
    expect(result.byId.get('A')?.column).toBe(0);
    expect(result.byId.get('B')?.column).toBe(1);
    expect(result.byId.get('C')?.column).toBe(2);
    expect(result.widths.notStarted).toBe(3);
  });

  it('starts every zone at its own first column', () => {
    const result = assignColumns([n('A', 'done'), n('B', 'running'), n('C', 'notStarted')]);
    expect(result.byId.get('A')).toEqual({ zone: 'done', column: 0 });
    expect(result.byId.get('B')).toEqual({ zone: 'running', column: 0 });
    expect(result.byId.get('C')).toEqual({ zone: 'notStarted', column: 0 });
    expect(result.widths).toEqual({ done: 1, running: 1, notStarted: 1 });
  });

  it('layers inside each zone and widens only that zone', () => {
    const result = assignColumns([
      n('A', 'done'),
      n('B', 'done', 'A'),
      n('C', 'running'),
      n('D', 'notStarted'),
      n('E', 'notStarted', 'D'),
    ]);
    expect(result.widths).toEqual({ done: 2, running: 1, notStarted: 2 });
    expect(result.byId.get('A')?.column).toBe(0);
    expect(result.byId.get('B')?.column).toBe(1);
    expect(result.byId.get('C')?.column).toBe(0);
    expect(result.byId.get('D')?.column).toBe(0);
    expect(result.byId.get('E')?.column).toBe(1);
  });

  it('a cross-zone edge adds no column', () => {
    const result = assignColumns([n('A', 'done'), n('B', 'notStarted', 'A')]);
    expect(result.byId.get('B')?.column).toBe(0);
    expect(result.widths.notStarted).toBe(1);
  });

  // The conflict case: a done task that follows a running one. Progress wins,
  // so the follower stays left and its edge draws backwards.
  it('lets progress beat dependency when the two disagree', () => {
    const result = assignColumns([n('A', 'done', 'B'), n('B', 'running')]);
    expect(result.byId.get('A')).toEqual({ zone: 'done', column: 0 });
    expect(result.byId.get('B')).toEqual({ zone: 'running', column: 0 });
  });

  it('pulls a follower left when its head finishes', () => {
    const running = assignColumns([n('A', 'running'), n('B', 'running', 'A')]);
    expect(running.byId.get('B')?.column).toBe(1);
    const finished = assignColumns([n('A', 'done'), n('B', 'running', 'A')]);
    expect(finished.byId.get('B')?.column).toBe(0);
  });

  // A follows cycle is a notebook error. The guard stops the recursion; the
  // cycle's columns are unspecified, but every node still gets one.
  it('terminates on a cycle inside one zone', () => {
    const result = assignColumns([n('A', 'running', 'B'), n('B', 'running', 'A')]);
    expect(Number.isFinite(result.byId.get('A')?.column)).toBe(true);
    expect(Number.isFinite(result.byId.get('B')?.column)).toBe(true);
  });

  it('ignores a follows id that is not in the input', () => {
    const result = assignColumns([n('B', 'notStarted', 'ghost')]);
    expect(result.byId.get('B')?.column).toBe(0);
    expect(result.widths.notStarted).toBe(1);
  });

  it('lets two independent chains share the same columns', () => {
    const result = assignColumns([
      n('A', 'notStarted'),
      n('B', 'notStarted', 'A'),
      n('C', 'notStarted'),
      n('D', 'notStarted', 'C'),
    ]);
    expect(result.byId.get('C')?.column).toBe(0);
    expect(result.byId.get('D')?.column).toBe(1);
    expect(result.widths.notStarted).toBe(2);
  });

  it('takes the longest path through a diamond', () => {
    const result = assignColumns([
      n('A', 'running'),
      n('B', 'running', 'A'),
      n('C', 'running', 'B'),
      n('D', 'running', 'A', 'C'),
    ]);
    expect(result.byId.get('D')?.column).toBe(3);
    expect(result.widths.running).toBe(4);
  });

  it('is deterministic', () => {
    const input = [n('A', 'done'), n('B', 'running', 'A'), n('C', 'notStarted', 'B')];
    expect(assignColumns(input)).toEqual(assignColumns(input));
  });

  // A column is a pure function of the constraint structure. All the order
  // sensitivity of the board lives in the row packer, not here.
  it('gives the same columns whatever order the input arrives in', () => {
    const input = [
      n('A', 'notStarted'),
      n('B', 'notStarted', 'A'),
      n('C', 'notStarted', 'B'),
      n('D', 'done'),
    ];
    const forwards = assignColumns(input);
    const backwards = assignColumns([...input].reverse());
    for (const id of ['A', 'B', 'C', 'D']) {
      expect(backwards.byId.get(id)).toEqual(forwards.byId.get(id));
    }
    expect(backwards.widths).toEqual(forwards.widths);
  });

  it('leaves every other column alone when a node is added', () => {
    const input = [n('A', 'notStarted'), n('B', 'notStarted', 'A')];
    const before = assignColumns(input);
    const after = assignColumns([...input, n('C', 'notStarted', 'A')]);
    for (const id of ['A', 'B']) {
      expect(after.byId.get(id)).toEqual(before.byId.get(id));
    }
  });

  it('gives an empty zone no width', () => {
    const result = assignColumns([n('A', 'done'), n('B', 'notStarted')]);
    expect(result.widths.running).toBe(0);
  });
});
