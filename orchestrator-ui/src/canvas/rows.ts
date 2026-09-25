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
 * the lowest row where that whole interval is free, in the order of their heads
 * in the input. A node that follows nothing is a one-cell track.
 * A branch, a second follower of one node, packs after that node's track and
 * searches from the row below that node.
 *
 * The caller must pass nodes in the order the rows are packed in. Only the
 * cut into tracks reads them by column.
 */
export function assignRows(nodes: readonly RowInput[]): ReadonlyMap<string, number> {
  const columnOf = new Map(nodes.map((n) => [n.id, n.column]));
  const next = new Map<string, string>();
  const continues = new Set<string>();
  const branchesFrom = new Map<string, string>();
  // A stable sort, so followers in one column keep their input order.
  for (const node of [...nodes].sort((a, b) => a.column - b.column)) {
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
  const taken = new Map<number, Set<number>>();
  const rows = new Map<string, number>();
  // Branches waiting for their parent's row, keyed by the parent.
  const waiting = new Map<string, RowInput[]>();
  const pack = (head: RowInput) => {
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
    for (const id of track) {
      rows.set(id, row);
      for (const branch of waiting.get(id) ?? []) pack(branch);
      waiting.delete(id);
    }
  };
  for (const head of nodes) {
    if (continues.has(head.id)) continue;
    const parent = branchesFrom.get(head.id);
    if (parent !== undefined && !rows.has(parent)) {
      waiting.set(parent, [...(waiting.get(parent) ?? []), head]);
    } else {
      pack(head);
    }
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
