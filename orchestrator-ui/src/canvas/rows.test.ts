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
  // The cut reads nodes by column, so B continues A although it is listed first.
  it('gives a follower listed before its predecessor the predecessor row', () => {
    expect(rowsOf([n('S0', 0), n('S1', 1, 'S0'), n('B', 2, 'A'), n('A', 1)])).toEqual({
      S0: 0,
      S1: 0,
      A: 1,
      B: 1,
    });
  });

  it('reserves the columns a track passes over', () => {
    expect(rowsOf([n('A', 0), n('B', 2, 'A'), n('S', 1)])).toEqual({ A: 0, B: 0, S: 1 });
  });

  // X's track holds row 0 of A's column, so A's track starts on row 1.
  it('branches a second follower below its parent and fills the cell above', () => {
    expect(
      rowsOf([n('X', 0), n('Y', 1, 'X'), n('A', 1), n('B', 2, 'A'), n('C', 2, 'A'), n('S', 2)]),
    ).toEqual({ X: 0, Y: 0, A: 1, B: 1, C: 2, S: 0 });
  });

  // Row 0 is free in column 2, but C there would read as B's follower, and
  // the edge from A would run behind B.
  it('puts a branch below its parent even past the end of the track', () => {
    expect(rowsOf([n('A', 0), n('B', 1, 'A'), n('C', 2, 'A')])).toEqual({ A: 0, B: 0, C: 1 });
  });

  // X continues B's track; C branches off B, whose row comes from A's track.
  it('puts a branch off a continuation below it, whatever the input order', () => {
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

  // T's head is leftmost, so its track packs before R and Q. Its tail sits
  // further right than P's, so it packs before P too.
  it('packs tracks leftmost-first, so a later chain does not drop below short tracks', () => {
    expect(
      rowsOf([n('P', 0), n('Q', 2), n('R', 1), n('S', 2, 'R'), n('T', 0), n('U', 1, 'T')]),
    ).toEqual({ T: 0, U: 0, P: 1, R: 1, S: 1, Q: 0 });
  });

  it('packs the longer of two tracks that start in one column first', () => {
    expect(rowsOf([n('A', 0), n('B', 0), n('C', 1, 'B'), n('D', 2, 'C')])).toEqual({
      B: 0,
      C: 0,
      D: 0,
      A: 1,
    });
  });

  // M merges into C, but C continues B, so M is a track of its own. It packs
  // straight after its component, not after S's longer track in the same column.
  it('packs a merge-in track straight after its component', () => {
    expect(
      rowsOf([
        n('A', 0),
        n('B', 1, 'A'),
        n('C', 2, 'B', 'M'),
        n('S', 0),
        n('T', 1, 'S'),
        n('U', 2, 'T'),
        n('M', 0),
      ]),
    ).toEqual({ A: 0, B: 0, C: 0, M: 1, S: 2, T: 2, U: 2 });
  });

  // Row 0 is free in column 3, but M there would read as Q's follower.
  it('packs a merge-in track no higher than its component', () => {
    expect(
      rowsOf([
        n('P', 0),
        n('Q', 2, 'P'),
        n('A', 2),
        n('B', 3, 'A'),
        n('C', 4, 'B', 'M'),
        n('M', 3),
      ]),
    ).toEqual({ P: 0, Q: 0, A: 1, B: 1, C: 1, M: 2 });
  });

  // A follows B from a lower column, so it cannot continue B's track, but the
  // two still form one component: B packs with A, before C.
  it('packs a backward follower with its component', () => {
    expect(
      rowsOf([
        n('X0', 0),
        n('X1', 1, 'X0'),
        n('X2', 2, 'X1'),
        n('A', 0, 'B'),
        n('C', 1),
        n('B', 1),
      ]),
    ).toEqual({ X0: 0, X1: 0, X2: 0, A: 1, B: 1, C: 2 });
  });

  // P and Q follow ids from another lane. Those ids join nothing, so C packs
  // before Q.
  it('does not join two tracks through ids outside the input', () => {
    expect(
      rowsOf([n('L0', 0), n('L1', 1, 'L0'), n('P', 0, 'gone1'), n('C', 1), n('Q', 1, 'gone2')]),
    ).toEqual({ L0: 0, L1: 0, P: 1, C: 1, Q: 2 });
  });

  it('terminates on a cycle', () => {
    expect(rowsOf([n('A', 1, 'B'), n('B', 0, 'A')])).toEqual({ A: 0, B: 0 });
  });
});
