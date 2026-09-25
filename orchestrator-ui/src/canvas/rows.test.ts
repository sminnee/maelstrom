import { describe, expect, it } from 'vitest';
import { assignRows, type RowInput } from './rows';

function n(id: string, column: number, ...follows: string[]): RowInput {
  return { id, column, follows };
}

/** Every row, as a plain object, so a missing or extra id fails too. */
function rowsOf(nodes: RowInput[]) {
  return Object.fromEntries(assignRows(nodes));
}

describe('assignRows', () => {
  // A done head in column 0 and a not-started follower in column 1, with
  // three not-started singletons already filling column 1's first rows.
  it('puts a follower in another zone on its predecessor row', () => {
    expect(rowsOf([n('S1', 1), n('S2', 1), n('S3', 1), n('A', 0), n('B', 1, 'A')])).toEqual({
      S1: 0,
      S2: 1,
      S3: 2,
      A: 3,
      B: 3,
    });
  });

  // Packed one node at a time, B would take row 0 before A had a row.
  it('gives a follower listed before its predecessor the predecessor row', () => {
    expect(rowsOf([n('S1', 0), n('B', 1, 'A'), n('A', 0)])).toEqual({ S1: 0, B: 1, A: 1 });
  });

  it('reserves the columns a track passes over', () => {
    expect(rowsOf([n('A', 0), n('B', 2, 'A'), n('S', 1)])).toEqual({ A: 0, B: 0, S: 1 });
  });

  // X holds row 0 of column 0, so A's track starts on row 1.
  it('branches a second follower below its parent and fills the cell above', () => {
    expect(rowsOf([n('X', 0), n('A', 0), n('B', 1, 'A'), n('C', 1, 'A'), n('S', 1)])).toEqual({
      X: 0,
      A: 1,
      B: 1,
      C: 2,
      S: 0,
    });
  });

  // Row 0 is free in column 2, but C there would read as B's follower, and
  // the edge from A would run behind B.
  it('puts a branch below its parent even past the end of the track', () => {
    expect(rowsOf([n('A', 0), n('B', 1, 'A'), n('C', 2, 'A')])).toEqual({ A: 0, B: 0, C: 1 });
  });

  // X and C branch off B, which has no row until A's track packs.
  it('packs a branch listed before its parent once the parent has a row', () => {
    expect(rowsOf([n('X', 2, 'B'), n('C', 2, 'B'), n('A', 0), n('B', 1, 'A')])).toEqual({
      X: 0,
      C: 1,
      A: 0,
      B: 0,
    });
  });

  // 4 also follows 1, which the path through 2 and 3 already implies.
  it('continues the nearest predecessor, not an implied one', () => {
    expect(rowsOf([n('1', 0), n('4', 3, '1', '3'), n('2', 1, '1'), n('3', 2, '2')])).toEqual({
      '1': 0,
      '2': 0,
      '3': 0,
      '4': 0,
    });
  });

  it('gives the track to the nearest follower', () => {
    expect(rowsOf([n('A', 0), n('C', 4, 'A'), n('B', 1, 'A')])).toEqual({ A: 0, B: 0, C: 1 });
  });

  // A done task that follows a running one sits left of it, so the edge
  // draws backwards and the two share no track.
  it('starts a new track on a backward edge and overlaps nothing', () => {
    expect(rowsOf([n('B', 1), n('A', 0, 'B'), n('C', 1)])).toEqual({ B: 0, A: 0, C: 1 });
  });

  it('terminates on a cycle', () => {
    expect(rowsOf([n('A', 1, 'B'), n('B', 0, 'A')])).toEqual({ A: 0, B: 0 });
  });
});
