/** One node as the engine sees it: an id, its global column, and what it follows. */
export interface RowInput {
  id: string;
  /** The board column: the zone's offset plus the node's column in that zone. */
  column: number;
  /** Ids this node follows. An id not in the same input set is ignored. */
  follows: readonly string[];
  /** The worktree box that holds this node. A node with none is loose. */
  box?: string | undefined;
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
 * the lowest row where that whole interval is free, leftmost head first. When
 * every component below is a single track and nothing branches, that order
 * takes the fewest rows: the most tracks on one column.
 * Between heads in one column, the track whose tail sits furthest right packs
 * first, and input order breaks what ties remain. A node that follows
 * nothing is a one-cell track. A branch, a second follower of one node, searches
 * from the row below that node.
 *
 * Tracks joined by follows edges, read in either direction, form a
 * component. When a component's leading track packs, the rest of the
 * component packs straight after it, in the same order, so a track that
 * merges into another sits near it. No track of a component searches above
 * the row of its leading track, where it would read as the follower of an
 * unrelated track. The next component packs first-fit from row 0 and fills
 * the gaps.
 *
 * A node can name a box. A track does not cross a box border: a node
 * continues a predecessor only in its own box, and two loose nodes are in one
 * box for this rule. A follower in another box starts a track of its own. It
 * is not a branch, so it can sit on the row of what it follows.
 *
 * All nodes of one box form one component, with or without an edge between
 * them. A box packs as one block. When the pass reaches the first track of a
 * box, it packs every track of that box on a grid of its own, by the rules
 * above. The block is the rectangle from the lowest column to the highest
 * column of the box, as many rows high as that pack. It goes first-fit onto
 * the lowest row where every cell of the rectangle is free, and it reserves
 * every cell, empty ones included. A loose node packs as a track and fills
 * the free cells round the boxes.
 *
 * A follows edge between two boxes reserves no cell. When the two blocks are
 * not in adjacent columns, a later track can take a cell between them, and the
 * edge then draws behind that card.
 */
export function assignRows(nodes: readonly RowInput[]): ReadonlyMap<string, number> {
  const columnOf = new Map(nodes.map((n) => [n.id, n.column]));
  const boxOf = new Map(nodes.map((n) => [n.id, n.box]));
  const next = new Map<string, string>();
  const continues = new Set<string>();
  const branchesFrom = new Map<string, string>();
  // A stable sort, so nodes in one column keep their input order.
  const byColumn = [...nodes].sort((a, b) => a.column - b.column);
  for (const node of byColumn) {
    const lower = node.follows
      .filter((id) => (columnOf.get(id) ?? node.column) < node.column)
      .filter((id) => boxOf.get(id) === node.box)
      .sort((a, b) => columnOf.get(b)! - columnOf.get(a)!);
    const predecessor = lower.find((id) => !next.has(id));
    if (predecessor === undefined) {
      if (lower.length > 0) branchesFrom.set(node.id, lower[0]!);
      continue;
    }
    next.set(predecessor, node.id);
    continues.add(node.id);
  }

  // Union-find over every follows edge, including those the track cut ignores,
  // and over the nodes of each box.
  const parentOf = new Map(nodes.map((n) => [n.id, n.id]));
  const find = (id: string): string => {
    let top = id;
    while (parentOf.get(top) !== top) top = parentOf.get(top)!;
    parentOf.set(id, top);
    return top;
  };
  for (const node of nodes) {
    for (const id of node.follows) {
      if (parentOf.has(id)) parentOf.set(find(id), find(node.id));
    }
  }
  const boxLead = new Map<string, string>();
  for (const node of nodes) {
    if (node.box === undefined) continue;
    const lead = boxLead.get(node.box);
    if (lead === undefined) boxLead.set(node.box, node.id);
    else parentOf.set(find(lead), find(node.id));
  }

  // A continuation always sits in a higher column, so a track cannot loop.
  // A branch's parent sits in a lower column, so it has a row first.
  const sorted = byColumn
    .filter((n) => !continues.has(n.id))
    .map((head) => {
      const ids = [head.id];
      for (let id = next.get(head.id); id !== undefined; id = next.get(id)) ids.push(id);
      const last = columnOf.get(ids[ids.length - 1]!)!;
      return { ids, first: head.column, last, box: head.box };
    })
    .sort((a, b) => a.first - b.first || b.last - a.last);
  // A map keeps insertion order, so a component's leading track places it.
  const components = new Map<string, typeof sorted>();
  for (const track of sorted) {
    const key = find(track.ids[0]!);
    const component = components.get(key) ?? [];
    component.push(track);
    components.set(key, component);
  }

  const taken: Taken = new Map();
  const rows = new Map<string, number>();
  for (const tracks of components.values()) {
    let floor: number | undefined;
    const placed = new Set<string>();
    for (const track of tracks) {
      if (track.box === undefined) {
        placeTracks([track], taken, rows, branchesFrom, floor ?? 0);
      } else if (!placed.has(track.box)) {
        placed.add(track.box);
        const block = tracks.filter((t) => t.box === track.box);
        placeBlock(block, taken, rows, branchesFrom, floor ?? 0);
      }
      floor ??= rows.get(track.ids[0]!)!;
    }
  }
  return rows;
}

type Taken = Map<number, Set<number>>;

interface Track {
  ids: string[];
  first: number;
  last: number;
  box: string | undefined;
}

/**
 * Put each track on the lowest free row at or below `floor`, in order. A
 * branch also stays below the row of its parent. Writes `taken` and `rows`.
 */
function placeTracks(
  tracks: readonly Track[],
  taken: Taken,
  rows: Map<string, number>,
  branchesFrom: ReadonlyMap<string, string>,
  floor: number,
) {
  for (const { ids, first, last } of tracks) {
    const parent = branchesFrom.get(ids[0]!);
    let row = Math.max(floor, parent === undefined ? 0 : rows.get(parent)! + 1);
    while (!isFree(taken, first, last, row)) row += 1;
    reserve(taken, first, last, row);
    for (const id of ids) rows.set(id, row);
  }
}

/** Put the tracks of one box on the board as one block, at or below `floor`. See `assignRows`. */
function placeBlock(
  tracks: readonly Track[],
  taken: Taken,
  rows: Map<string, number>,
  branchesFrom: ReadonlyMap<string, string>,
  floor: number,
) {
  const local = new Map<string, number>();
  placeTracks(tracks, new Map(), local, branchesFrom, 0);
  const first = Math.min(...tracks.map((t) => t.first));
  const last = Math.max(...tracks.map((t) => t.last));
  const height = Math.max(...local.values()) + 1;
  const fits = (top: number) => {
    for (let row = top; row < top + height; row += 1) {
      if (!isFree(taken, first, last, row)) return false;
    }
    return true;
  };
  let top = floor;
  while (!fits(top)) top += 1;
  for (let row = top; row < top + height; row += 1) reserve(taken, first, last, row);
  for (const [id, row] of local) rows.set(id, top + row);
}

/** Take every cell from `first` to `last` on `row`. */
function reserve(taken: Taken, first: number, last: number, row: number) {
  for (let column = first; column <= last; column += 1) {
    const used = taken.get(column) ?? new Set<number>();
    used.add(row);
    taken.set(column, used);
  }
}

/** True when no cell from `first` to `last` on `row` is taken. */
function isFree(taken: Taken, first: number, last: number, row: number) {
  for (let column = first; column <= last; column += 1) {
    if (taken.get(column)?.has(row)) return false;
  }
  return true;
}
