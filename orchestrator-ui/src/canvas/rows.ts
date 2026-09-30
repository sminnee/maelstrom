/** One node as the engine sees it: an id, its global column, and what it follows. */
export interface RowInput {
  id: string;
  /** The board column: the zone's offset plus the node's column in that zone. */
  column: number;
  /** Ids this node follows. An id not in the same input set is ignored. */
  follows: readonly string[];
}

/**
 * Assign every node a row. Pure and deterministic.
 *
 * A row holds a track: a run of nodes where each one follows the one to its
 * left. A node continues the track of its nearest predecessor: the id in its
 * follows list that is in the input, sits in the highest lower column, and has
 * no continuation yet. Nearer followers claim first, so a far follower, or one
 * whose edge a longer path already implies, does not take the track. Every
 * other node starts a track of its own.
 *
 * A track reserves every cell from its head's column to its tail's column, so
 * its edges draw straight and never behind a card. Tracks pack first-fit onto
 * the lowest row where that whole interval is free, leftmost head first. Without
 * branches, that order takes the fewest rows: the most tracks on one column.
 * Input order only breaks ties between heads in one column. A node that follows
 * nothing is a one-cell track. A branch, a second follower of one node, searches
 * from the row below that node.
 */
export function assignRows(nodes: readonly RowInput[]): ReadonlyMap<string, number> {
  const columnOf = new Map(nodes.map((n) => [n.id, n.column]));
  const next = new Map<string, string>();
  const continues = new Set<string>();
  const branchesFrom = new Map<string, string>();
  // A stable sort, so nodes in one column keep their input order.
  const byColumn = [...nodes].sort((a, b) => a.column - b.column);
  for (const node of byColumn) {
    const lower = node.follows
      .filter((id) => (columnOf.get(id) ?? node.column) < node.column)
      .sort((a, b) => columnOf.get(b)! - columnOf.get(a)!);
    const predecessor = lower.find((id) => !next.has(id));
    if (predecessor === undefined) {
      if (lower.length > 0) branchesFrom.set(node.id, lower[0]!);
      continue;
    }
    next.set(predecessor, node.id);
    continues.add(node.id);
  }

  // A continuation always sits in a higher column, so a track cannot loop.
  // A branch's parent sits in a lower column, so it has a row first.
  const taken = new Map<number, Set<number>>();
  const rows = new Map<string, number>();
  for (const head of byColumn.filter((n) => !continues.has(n.id))) {
    const track = [head.id];
    for (let id = next.get(head.id); id !== undefined; id = next.get(id)) track.push(id);
    const first = head.column;
    const last = columnOf.get(track[track.length - 1]!)!;
    const parent = branchesFrom.get(head.id);
    let row = parent === undefined ? 0 : rows.get(parent)! + 1;
    while (!isFree(taken, first, last, row)) row += 1;
    for (let column = first; column <= last; column += 1) {
      const used = taken.get(column) ?? new Set<number>();
      used.add(row);
      taken.set(column, used);
    }
    for (const id of track) rows.set(id, row);
  }
  return rows;
}

/** True when no cell from `first` to `last` on `row` is taken. */
function isFree(taken: Map<number, Set<number>>, first: number, last: number, row: number) {
  for (let column = first; column <= last; column += 1) {
    if (taken.get(column)?.has(row)) return false;
  }
  return true;
}
