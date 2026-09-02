import type { Graph } from '../selectors/graph';

export interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Layout {
  /** Absolute position and size of each group band. */
  groups: Record<string, Box>;
  /** Node position relative to its group's origin. */
  nodes: Record<string, { x: number; y: number }>;
  nodeSize: { width: number; height: number };
}

export const NODE = { width: 220, height: 76 };
const GAP_X = 56;
const GAP_Y = 14;
const LANE_PAD = 20;
const LANE_HEADER = 30;
const LANE_GAP = 28;
const MIN_LANE_WIDTH = NODE.width + LANE_PAD * 2;

/**
 * Hand-rolled swimlanes. One band per group, stacked in group order. Inside a
 * band, x is the depth along the follows edges within that band, and y is the
 * row of the followed node when it is free, else the next free row. A follows
 * cycle is a notebook error: the guard only stops the recursion, and the
 * cycle's columns are unspecified.
 */
export function layoutSwimlanes(graph: Graph): Layout {
  const groups: Record<string, Box> = {};
  const nodes: Record<string, { x: number; y: number }> = {};
  let laneY = 0;
  let maxWidth = MIN_LANE_WIDTH;

  const groupOf = new Map(graph.nodes.map((n) => [n.id, n.groupId]));
  const followed = new Map<string, string[]>();
  for (const edge of graph.edges) {
    if (groupOf.get(edge.source) !== groupOf.get(edge.target)) continue;
    followed.set(edge.target, [...(followed.get(edge.target) ?? []), edge.source]);
  }
  const depth = depths(
    graph.nodes.map((n) => n.id),
    followed,
  );

  const bands: { id: string; height: number }[] = [];
  for (const group of graph.groups) {
    const taken = new Map<number, Set<number>>();
    const slotOf = new Map<string, number>();
    let rows = 0;
    let columns = 0;
    for (const id of group.nodeIds) {
      const column = depth.get(id) ?? 0;
      const used = taken.get(column) ?? new Set<number>();
      taken.set(column, used);
      const wanted =
        (followed.get(id) ?? []).map((s) => slotOf.get(s)).find((s) => s !== undefined) ?? 0;
      let slot = wanted;
      while (used.has(slot)) slot += 1;
      used.add(slot);
      slotOf.set(id, slot);
      nodes[id] = {
        x: LANE_PAD + column * (NODE.width + GAP_X),
        y: LANE_HEADER + LANE_PAD + slot * (NODE.height + GAP_Y),
      };
      rows = Math.max(rows, slot + 1);
      columns = Math.max(columns, column + 1);
    }
    const width = LANE_PAD * 2 + columns * NODE.width + Math.max(0, columns - 1) * GAP_X;
    const height = LANE_HEADER + LANE_PAD * 2 + rows * NODE.height + Math.max(0, rows - 1) * GAP_Y;
    maxWidth = Math.max(maxWidth, width);
    bands.push({ id: group.id, height });
  }
  for (const band of bands) {
    groups[band.id] = { x: 0, y: laneY, width: maxWidth, height: band.height };
    laneY += band.height + LANE_GAP;
  }
  return { groups, nodes, nodeSize: { ...NODE } };
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
