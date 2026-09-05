import { zoneForState } from '../protocol/phase';
import type { Graph } from '../selectors/graph';
import { assignColumns, ZONES, type Zone } from './columns';

export interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** One stage of progress, as a vertical stripe across every lane. */
export interface ZoneBand {
  zone: Zone;
  /** Left edge, in the same space as a group box's x. */
  x: number;
  /** Columns this zone holds board-wide. 0 when no lane uses it, and it draws nothing. */
  columns: number;
}

export interface Layout {
  /** Absolute position and size of each group band. */
  groups: Record<string, Box>;
  /** Node position relative to its group's origin. */
  nodes: Record<string, { x: number; y: number }>;
  nodeSize: { width: number; height: number };
  /** Always three entries, in board order, even when a zone is empty. */
  zones: ZoneBand[];
  /** Every lane is this wide, so the board is too. */
  boardWidth: number;
}

export const NODE = { width: 220, height: 76 };
const GAP_X = 56;
const GAP_Y = 14;
const LANE_PAD = 20;
const LANE_HEADER = 30;
const LANE_GAP = 28;

/**
 * Hand-rolled swimlanes. One band per group, stacked in group order. Inside a
 * band, x is the node's progress zone plus its depth along the follows edges
 * within that zone, and y is the row of the followed node when it is free,
 * else the next free row. See `web/DESIGN.md` for why the zones align.
 */
export function layoutSwimlanes(graph: Graph): Layout {
  const groups: Record<string, Box> = {};
  const nodes: Record<string, { x: number; y: number }> = {};
  let laneY = 0;

  const zoneOf = new Map(graph.nodes.map((n) => [n.id, zoneForState(n.state)]));
  const followsOf = new Map(graph.nodes.map((n) => [n.id, [] as string[]]));
  for (const edge of graph.edges) followsOf.get(edge.target)?.push(edge.source);

  // Each lane is scored on its own: the engine sees one lane, so it never
  // needs a notion of groups. A cross-lane edge falls out as an unknown id.
  const columnsPerGroup = new Map(
    graph.groups.map((group) => [
      group.id,
      assignColumns(
        group.nodeIds.map((id) => ({
          id,
          zone: zoneOf.get(id) ?? 'notStarted',
          follows: followsOf.get(id) ?? [],
        })),
      ),
    ]),
  );

  // The board's zone widths, so a boundary sits at the same x in every lane.
  // A zone no lane uses takes no columns and collapses to nothing.
  const boardWidths: Record<Zone, number> = { done: 0, running: 0, notStarted: 0 };
  for (const result of columnsPerGroup.values()) {
    for (const zone of ZONES) boardWidths[zone] = Math.max(boardWidths[zone], result.widths[zone]);
  }
  const offsets: Record<Zone, number> = { done: 0, running: 0, notStarted: 0 };
  let boardColumns = 0;
  for (const zone of ZONES) {
    offsets[zone] = boardColumns;
    boardColumns += boardWidths[zone];
  }
  const laneWidth = laneWidthFor(boardColumns);
  // A boundary falls in the middle of the gutter between two columns, so the
  // stripe lines up with the gap the operator already sees.
  const zones = ZONES.map((zone) => ({
    zone,
    x: LANE_PAD + offsets[zone] * (NODE.width + GAP_X) - GAP_X / 2,
    columns: boardWidths[zone],
  }));

  const bands: { id: string; height: number }[] = [];
  for (const group of graph.groups) {
    const header = group.kind === 'none' ? 0 : LANE_HEADER;
    const placed = columnsPerGroup.get(group.id)!.byId;
    const taken = new Map<number, Set<number>>();
    const slotOf = new Map<string, number>();
    let rows = 0;
    for (const id of group.nodeIds) {
      const at = placed.get(id) ?? { zone: 'notStarted' as Zone, column: 0 };
      const column = offsets[at.zone] + at.column;
      const used = taken.get(column) ?? new Set<number>();
      taken.set(column, used);
      // A done predecessor's row is no home for a not-started follower several
      // columns away, so only a same-zone predecessor offers one.
      const wanted =
        (followsOf.get(id) ?? [])
          .filter((source) => placed.get(source)?.zone === at.zone)
          .map((source) => slotOf.get(source))
          .find((slot) => slot !== undefined) ?? 0;
      let slot = wanted;
      while (used.has(slot)) slot += 1;
      used.add(slot);
      slotOf.set(id, slot);
      nodes[id] = {
        x: LANE_PAD + column * (NODE.width + GAP_X),
        y: header + LANE_PAD + slot * (NODE.height + GAP_Y),
      };
      rows = Math.max(rows, slot + 1);
    }
    const height = header + LANE_PAD * 2 + rows * NODE.height + Math.max(0, rows - 1) * GAP_Y;
    bands.push({ id: group.id, height });
  }
  for (const band of bands) {
    groups[band.id] = { x: 0, y: laneY, width: laneWidth, height: band.height };
    laneY += band.height + LANE_GAP;
  }
  return { groups, nodes, nodeSize: { ...NODE }, zones, boardWidth: laneWidth };
}

/** A lane holding `columns` columns, never narrower than one node. */
function laneWidthFor(columns: number): number {
  const width = Math.max(1, columns);
  return LANE_PAD * 2 + width * NODE.width + (width - 1) * GAP_X;
}
