/** One edge as the reducer sees it: an id, and the pair it joins. */
export interface ReduceEdge {
  id: string;
  source: string;
  target: string;
}

/** One node as the reducer sees it: an id, and whether its build has ended. */
export interface ReduceNode {
  id: string;
  /**
   * Whether the node's task's build has ended: `buildEnded` in
   * `protocol/entities.ts`. Mirrors `build_ended` in `task.py`, which
   * `is_actionable` gates on, so a cancelled task still gates. False for a
   * node that stands for no task.
   */
  buildEnded: boolean;
}

/**
 * Drop every edge another path already implies. Pure and deterministic.
 *
 * Display only: the caller keeps the full edge list for layout. See "The board
 * draws fewer wires than the notebook holds" in `docs/dev/orchestrator-ui.md`
 * for the rule.
 */
export function reduceEdges(
  edges: readonly ReduceEdge[],
  nodes: readonly ReduceNode[],
): ReduceEdge[] {
  const endedById = new Map(nodes.map((n) => [n.id, n.buildEnded]));
  // Forward adjacency over gating edges only: a build-ended source cannot carry an
  // implication, so it never joins a path that hides anything.
  const outOf = new Map<string, string[]>();
  for (const edge of edges) {
    if (endedById.get(edge.source) === true) continue;
    const from = outOf.get(edge.source);
    if (from) from.push(edge.target);
    else outOf.set(edge.source, [edge.target]);
  }

  return edges.filter((edge) => !impliedByDetour(edge, outOf, endedById));
}

/**
 * Whether a path from the edge's source reaches its target without taking the
 * edge itself. The first hop is what makes it a detour; every node along the
 * way must still be gating, which the adjacency above already ensures.
 */
function impliedByDetour(
  edge: ReduceEdge,
  outOf: ReadonlyMap<string, string[]>,
  endedById: ReadonlyMap<string, boolean>,
): boolean {
  const seen = new Set<string>([edge.source]);
  const queue = (outOf.get(edge.source) ?? []).filter((id) => id !== edge.target);
  while (queue.length > 0) {
    const id = queue.shift()!;
    if (id === edge.target) return true;
    // The walk stops at a build-ended node: it releases its followers, so no
    // implication runs through it. The visited set ends a cycle.
    if (seen.has(id) || endedById.get(id) === true) continue;
    seen.add(id);
    queue.push(...(outOf.get(id) ?? []));
  }
  return false;
}
