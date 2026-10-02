import { zoneForState, ZONES, type Zone } from '../protocol/progress';
import type { Worktree } from '../protocol/entities';
import type { Graph } from '../selectors/graph';
import { assignColumns } from './columns';
import { assignRows } from './rows';

export interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** One stage of progress, as a vertical stripe across every lane. */
export interface ZoneBand {
  zone: Zone;
  /** The left edge of the zone's first column, in the same space as a group box's x. */
  x: number;
  /** From `x` to the right edge of the zone's last column. 0 when the zone holds no column. */
  width: number;
  /** Columns this zone holds board-wide. 0 when no lane uses it, and it draws nothing. */
  columns: number;
}

/** One **Worktree box**, relative to its lane's origin as a node is. */
export interface WorktreeBox extends Box {
  worktree: Worktree;
  /** True for a box in the strip of worktrees that hold no node. */
  empty: boolean;
}

export interface Layout {
  /** Absolute position and size of each group band. */
  groups: Record<string, Box>;
  /** Node position relative to its group's origin. */
  nodes: Record<string, { x: number; y: number }>;
  nodeSize: { width: number; height: number };
  /** The worktree boxes of each lane, by group id. Only a project lane has any. */
  worktreeBoxes: Record<string, WorktreeBox[]>;
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
/** The label strip at the top of a worktree box. */
const BOX_HEADER = 18;
/**
 * Under `GAP_X / 2`, so a box reaches no zone boundary and no neighbour column.
 * Under 10 as well: React Flow pads a group node by 10, so the lane's border
 * draws that far inside the lane, and a wider box would sit on it.
 */
const BOX_PAD = 6;
const BOX_GAP = 14;
/** A box with no node: wide enough for the longest NATO name, as high as its label. */
const EMPTY_BOX = { width: 104, height: 24 };
const EMPTY_GAP = 8;

/**
 * Hand-rolled swimlanes. One band per group, stacked in group order. Inside a
 * band, x is the node's progress zone plus its depth along the follows edges
 * within that zone, and y is the row of the node's track: a follower sits on
 * the row of what it follows. See `orchestrator-ui/DESIGN.md` for why the zones align.
 */
export function layoutSwimlanes(graph: Graph): Layout {
  const groups: Record<string, Box> = {};
  const nodes: Record<string, { x: number; y: number }> = {};
  const worktreeBoxes: Record<string, WorktreeBox[]> = {};
  let laneY = 0;

  const zoneOf = new Map(graph.nodes.map((n) => [n.id, zoneForState(n.progress.state)]));
  const worktreeOf = new Map(graph.nodes.map((n) => [n.id, n.worktree]));
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
    x: columnX(offsets[zone]),
    width: Math.max(0, boardWidths[zone] * (NODE.width + GAP_X) - GAP_X),
    columns: boardWidths[zone],
  }));

  const bands: { id: string; height: number }[] = [];
  for (const group of graph.groups) {
    const header = group.kind === 'none' ? 0 : LANE_HEADER;
    const placed = columnsPerGroup.get(group.id)!.byId;
    const columnOf = new Map(
      group.nodeIds.map((id) => {
        const at = placed.get(id) ?? { zone: 'notStarted' as Zone, column: 0 };
        return [id, offsets[at.zone] + at.column];
      }),
    );
    // A project lane stacks one section per worktree, each with rows of its
    // own. Every other lane is one section.
    const sections = new Map<Worktree | undefined, string[]>();
    for (const id of group.nodeIds) {
      const key = group.kind === 'project' ? worktreeOf.get(id) : undefined;
      const ids = sections.get(key);
      if (ids) ids.push(id);
      else sections.set(key, [id]);
    }
    const boxes: WorktreeBox[] = [];
    const top = header + LANE_PAD;
    let y = top;
    for (const [worktree, ids] of sections) {
      // A follows edge that crosses sections falls out as an unknown id, as a
      // cross-lane edge does.
      const rowOf = assignRows(
        ids.map((id) => ({ id, column: columnOf.get(id)!, follows: followsOf.get(id) ?? [] })),
      );
      const columns = ids.map((id) => columnOf.get(id)!);
      const rows = Math.max(...rowOf.values()) + 1;
      const rowsHeight = rows * NODE.height + (rows - 1) * GAP_Y;
      const rowsY = worktree ? y + BOX_HEADER + BOX_PAD : y;
      for (const id of ids) {
        nodes[id] = {
          x: columnX(columnOf.get(id)!),
          y: rowsY + rowOf.get(id)! * (NODE.height + GAP_Y),
        };
      }
      const bottom = rowsY + rowsHeight + (worktree ? BOX_PAD : 0);
      if (worktree) {
        const left = columnX(Math.min(...columns)) - BOX_PAD;
        const right = columnX(Math.max(...columns)) + NODE.width + BOX_PAD;
        boxes.push({ worktree, empty: false, x: left, y, width: right - left, height: bottom - y });
      }
      y = bottom + BOX_GAP;
    }
    // The strip of empty boxes: left to right between the edges a full box
    // would have, so the strip lines up with the boxes above it.
    const stripLeft = LANE_PAD - BOX_PAD;
    let x = stripLeft;
    for (const worktree of group.emptyWorktrees) {
      if (x > stripLeft && x + EMPTY_BOX.width > laneWidth - stripLeft) {
        x = stripLeft;
        y += EMPTY_BOX.height + EMPTY_GAP;
      }
      boxes.push({ worktree, empty: true, x, y, ...EMPTY_BOX });
      x += EMPTY_BOX.width + EMPTY_GAP;
    }
    if (group.emptyWorktrees.length > 0) y += EMPTY_BOX.height + BOX_GAP;
    worktreeBoxes[group.id] = boxes;
    const height = Math.max(top, y - BOX_GAP) + LANE_PAD;
    bands.push({ id: group.id, height });
  }
  for (const band of bands) {
    groups[band.id] = { x: 0, y: laneY, width: laneWidth, height: band.height };
    laneY += band.height + LANE_GAP;
  }
  return { groups, nodes, nodeSize: { ...NODE }, worktreeBoxes, zones, boardWidth: laneWidth };
}

/** The left edge of a board column, from the lane's origin. */
function columnX(column: number): number {
  return LANE_PAD + column * (NODE.width + GAP_X);
}

/** A lane holding `columns` columns, never narrower than one node. */
function laneWidthFor(columns: number): number {
  const width = Math.max(1, columns);
  return LANE_PAD * 2 + width * NODE.width + (width - 1) * GAP_X;
}
