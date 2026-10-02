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
  /** The worktree boxes of each lane, by group id. */
  worktreeBoxes: Record<string, WorktreeBox[]>;
  /** Always three entries, in board order, even when a zone is empty. */
  zones: ZoneBand[];
  /** Every lane is this wide, so the board is too. */
  boardWidth: number;
}

export const NODE = { width: 220, height: 76 };
const GAP_X = 56;
const GAP_Y = 14;
/**
 * The padding of a lane's node, set in `Canvas.module.css`: a lane's border
 * draws this far inside the lane's own rectangle.
 */
const LANE_INSET = 10;
const LANE_GAP = 28;
/**
 * The one gap of a lane: from the lane's border to a worktree box, from
 * the box to a node, and between two boxes. Under `GAP_X / 2`, so a box reaches
 * no zone boundary and no neighbour column. Wide enough that the lane's label
 * and the first box's label, each centred on its border, do not touch.
 */
const BOX_PAD = 16;
/**
 * A box with no node: as wide as a node, so its label has room for the branch
 * and the strip keeps the column grid, and as high as its label.
 */
const EMPTY_BOX = { width: NODE.width, height: 24 };
const EMPTY_GAP = 8;
/** The occupant of a cell that holds a node with no worktree box. */
const LOOSE = '';

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
  // A lane leaves room for a worktree box between its border and its nodes.
  // Its label needs no header, because the gap above the first box holds it.
  const pad = LANE_INSET + BOX_PAD * 2;
  const columnX = (column: number) => pad + column * (NODE.width + GAP_X);
  const width = Math.max(1, boardColumns);
  // A lane is never narrower than one node.
  const laneWidth = pad * 2 + width * NODE.width + (width - 1) * GAP_X;
  const zones = ZONES.map((zone) => ({
    zone,
    x: columnX(offsets[zone]),
    width: Math.max(0, boardWidths[zone] * (NODE.width + GAP_X) - GAP_X),
    columns: boardWidths[zone],
  }));

  const bands: { id: string; height: number }[] = [];
  for (const group of graph.groups) {
    const placed = columnsPerGroup.get(group.id)!.byId;
    const columnOf = new Map(
      group.nodeIds.map((id) => {
        const at = placed.get(id) ?? { zone: 'notStarted' as Zone, column: 0 };
        return [id, offsets[at.zone] + at.column];
      }),
    );
    // One packing pass for the lane. A node names its worktree as its box, so
    // the nodes of one worktree pack as one block.
    const boxOf = (id: string) => worktreeOf.get(id);
    const rowOf = assignRows(
      group.nodeIds.map((id) => ({
        id,
        column: columnOf.get(id)!,
        follows: followsOf.get(id) ?? [],
        box: boxOf(id)?.id,
      })),
    );
    // The cells of each worktree, in the order of their oldest node. A box is
    // the rectangle of its nodes: the engine reserved every cell of it.
    const held = new Map<Worktree, { columns: number[]; rows: number[] }>();
    // What sits in each cell: a box, by worktree id, or a node with no box.
    const occupants = new Map<string, string>();
    const at = (row: number, column: number) => `${row}:${column}`;
    let lastRow = -1;
    for (const id of group.nodeIds) {
      const row = rowOf.get(id)!;
      const column = columnOf.get(id)!;
      lastRow = Math.max(lastRow, row);
      const worktree = boxOf(id);
      if (!worktree) {
        occupants.set(at(row, column), LOOSE);
        continue;
      }
      const cells = held.get(worktree) ?? { columns: [], rows: [] };
      cells.columns.push(column);
      cells.rows.push(row);
      held.set(worktree, cells);
    }
    const spans = [...held].map(([worktree, cells]) => ({
      worktree,
      left: Math.min(...cells.columns),
      right: Math.max(...cells.columns),
      first: Math.min(...cells.rows),
      last: Math.max(...cells.rows),
    }));
    for (const span of spans) {
      for (let row = span.first; row <= span.last; row += 1) {
        for (let column = span.left; column <= span.right; column += 1) {
          occupants.set(at(row, column), span.worktree.id);
        }
      }
    }
    // The y of each row. A row gap grows to hold the box borders that meet
    // in it: the widest need of any column sets the gap for the whole lane.
    const edge = LANE_INSET + BOX_PAD;
    const top = edge;
    const rowY: number[] = [];
    for (let row = 0; row <= lastRow; row += 1) {
      let gap = row === 0 ? 0 : GAP_Y;
      for (let column = 0; column < width; column += 1) {
        const above = row === 0 ? undefined : occupants.get(at(row - 1, column));
        const below = occupants.get(at(row, column));
        if (above === below) continue;
        const borders = [above, below].filter((o) => o !== undefined && o !== LOOSE).length;
        if (borders === 0) continue;
        // One more gap keeps a border clear of what sits on its other side.
        const facing = above !== undefined && below !== undefined ? 1 : 0;
        gap = Math.max(gap, (borders + facing) * BOX_PAD);
      }
      rowY.push(row === 0 ? top + gap : rowY[row - 1]! + NODE.height + gap);
    }
    for (const id of group.nodeIds) {
      nodes[id] = { x: columnX(columnOf.get(id)!), y: rowY[rowOf.get(id)!]! };
    }
    const boxes: WorktreeBox[] = spans.map(({ worktree, left, right, first, last }) => {
      const x = columnX(left) - BOX_PAD;
      const boxY = rowY[first]! - BOX_PAD;
      return {
        worktree,
        empty: false,
        x,
        y: boxY,
        width: columnX(right) + NODE.width + BOX_PAD - x,
        height: rowY[last]! + NODE.height + BOX_PAD - boxY,
      };
    });
    // Below the lowest node or box, one gap down.
    const lowest = Math.max(
      lastRow < 0 ? top - BOX_PAD : rowY[lastRow]! + NODE.height,
      ...boxes.map((box) => box.y + box.height),
    );
    let y = lowest + BOX_PAD;
    // The strip of empty boxes: left to right between the edges a full box has.
    const stripLeft = edge;
    let x = stripLeft;
    for (const worktree of group.emptyWorktrees) {
      if (x > stripLeft && x + EMPTY_BOX.width > laneWidth - stripLeft) {
        x = stripLeft;
        y += EMPTY_BOX.height + EMPTY_GAP;
      }
      boxes.push({ worktree, empty: true, x, y, ...EMPTY_BOX });
      x += EMPTY_BOX.width + EMPTY_GAP;
    }
    if (group.emptyWorktrees.length > 0) y += EMPTY_BOX.height + BOX_PAD;
    worktreeBoxes[group.id] = boxes;
    const height = Math.max(top, y - BOX_PAD) + edge;
    bands.push({ id: group.id, height });
  }
  for (const band of bands) {
    groups[band.id] = { x: 0, y: laneY, width: laneWidth, height: band.height };
    laneY += band.height + LANE_GAP;
  }
  return { groups, nodes, nodeSize: { ...NODE }, worktreeBoxes, zones, boardWidth: laneWidth };
}
