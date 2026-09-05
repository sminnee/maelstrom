/** Where a node sits in the left-to-right progression of work. */
export type Zone = 'done' | 'running' | 'notStarted';

/** The zones in board order, leftmost first. The index is the progress rank. */
export const ZONES = ['done', 'running', 'notStarted'] as const;

/** One node as the engine sees it: an id, a zone, and what it follows. */
export interface ColumnInput {
  id: string;
  zone: Zone;
  /** Ids this node follows. An id not in the same input set is ignored. */
  follows: readonly string[];
}

export interface ColumnResult {
  /** Every input id, mapped to its zone and its column within that zone. */
  byId: ReadonlyMap<string, { zone: Zone; column: number }>;
  /** Columns each zone uses in this lane. 0 when the zone holds nothing. */
  widths: Readonly<Record<Zone, number>>;
}

/**
 * Assign every node a zone and a column within it. Pure and deterministic.
 *
 * Two rules make a column. If A follows B, A sits right of B. If A is at a
 * later progress stage than B, A sits right of B. When the two conflict,
 * progress wins: a done task that follows a running one stays in the done
 * zone, and its edge draws backwards.
 *
 * That ruling is what keeps this short. A zone is absolute, so a follows edge
 * that crosses zones is already satisfied by the zone placement whichever way
 * it points, and constrains nothing. Only same-zone edges layer, so the
 * column is a per-zone longest path. A follows cycle is a notebook error: the
 * guard only stops the recursion, and the cycle's columns are unspecified.
 *
 * The caller must pass nodes in the order the rows are packed in, because the
 * result is a map and the caller reads it back in its own order. This engine
 * never sorts.
 */
export function assignColumns(nodes: readonly ColumnInput[]): ColumnResult {
  const zoneOf = new Map(nodes.map((n) => [n.id, n.zone]));
  const followed = new Map<string, string[]>();
  for (const node of nodes) {
    // An id outside the input, and an id in another zone, impose nothing.
    followed.set(
      node.id,
      node.follows.filter((id) => zoneOf.get(id) === node.zone),
    );
  }
  const depth = depths(
    nodes.map((n) => n.id),
    followed,
  );

  const byId = new Map<string, { zone: Zone; column: number }>();
  const widths: Record<Zone, number> = { done: 0, running: 0, notStarted: 0 };
  for (const node of nodes) {
    const column = depth.get(node.id) ?? 0;
    byId.set(node.id, { zone: node.zone, column });
    widths[node.zone] = Math.max(widths[node.zone], column + 1);
  }
  return { byId, widths };
}

/** Longest-path depth of each node along the given follows edges. */
function depths(ids: string[], followed: Map<string, string[]>): Map<string, number> {
  const memo = new Map<string, number>();
  const visiting = new Set<string>();
  const depthOf = (id: string): number => {
    const known = memo.get(id);
    if (known !== undefined) return known;
    if (visiting.has(id)) return 0;
    visiting.add(id);
    const sources = followed.get(id) ?? [];
    const d = sources.length === 0 ? 0 : 1 + Math.max(...sources.map(depthOf));
    visiting.delete(id);
    memo.set(id, d);
    return d;
  };
  for (const id of ids) depthOf(id);
  return memo;
}
