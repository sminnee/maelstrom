import type { TaskStatus } from '../protocol/entities';

/**
 * Whether a task releases what follows it. Mirrors `is_done` in `task.py`,
 * which `is_actionable` gates on: `done` alone releases, so a cancelled task
 * still gates. Whether it should is an open question in `CONTEXT.md`.
 */
function isDone(status: TaskStatus | undefined): boolean {
  return status === 'done';
}

/** One edge as the reducer sees it: an id, and the pair it joins. */
export interface ReduceEdge {
  id: string;
  source: string;
  target: string;
}

/** One node as the reducer sees it: an id, and the status that decides gating. */
export interface ReduceNode {
  id: string;
  /** The task's status, or undefined for a node that stands for no task. */
  status: TaskStatus | undefined;
}

/**
 * Drop every edge another path already implies. Pure and deterministic.
 *
 * Display only: the caller keeps the full edge list for layout. See "The board
 * draws fewer wires than the notebook holds" in `docs/dev/orchestrator-ui.md`
 * for the rule, and why `done` is the only status that releases a follower.
 */
export function reduceEdges(
  edges: readonly ReduceEdge[],
  nodes: readonly ReduceNode[],
): ReduceEdge[] {
  const doneById = new Map(nodes.map((n) => [n.id, isDone(n.status)]));
  // Forward adjacency over gating edges only: a done source cannot carry an
  // implication, so it never joins a path that hides anything.
  const outOf = new Map<string, string[]>();
  for (const edge of edges) {
    if (doneById.get(edge.source) === true) continue;
    const from = outOf.get(edge.source);
    if (from) from.push(edge.target);
    else outOf.set(edge.source, [edge.target]);
  }

  return edges.filter((edge) => !impliedByDetour(edge, outOf, doneById));
}

/**
 * Whether a path from the edge's source reaches its target without taking the
 * edge itself. The first hop is what makes it a detour; every node along the
 * way must still be gating, which the adjacency above already ensures.
 */
function impliedByDetour(
  edge: ReduceEdge,
  outOf: ReadonlyMap<string, string[]>,
  doneById: ReadonlyMap<string, boolean>,
): boolean {
  const seen = new Set<string>([edge.source]);
  const queue = (outOf.get(edge.source) ?? []).filter((id) => id !== edge.target);
  while (queue.length > 0) {
    const id = queue.shift()!;
    if (id === edge.target) return true;
    // The walk stops at a done node: it releases its followers, so no
    // implication runs through it. The visited set ends a cycle.
    if (seen.has(id) || doneById.get(id) === true) continue;
    seen.add(id);
    queue.push(...(outOf.get(id) ?? []));
  }
  return false;
}
