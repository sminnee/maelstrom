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
/**
 * The padding of a lane's node, set in `Canvas.module.css`: a lane's border
 * draws this far inside the lane's own rectangle.
 */
const LANE_INSET = 10;
/** From the lane's edge to a node, in a lane with no worktree box. */
const LANE_PAD = 20;
/**
 * Room above the first row for a lane's label, which sits on the lane's top
 * border. A worktree lane's label carries a button, which hangs below the border.
 */
const LANE_HEADER = 6;
const LANE_GAP = 28;
/**
 * The one gap of a project lane: from the lane's border to a worktree box, from
 * the box to a node, and between two boxes. Under `GAP_X / 2`, so a box reaches
 * no zone boundary and no neighbour column. Wide enough that the lane's label
 * and the first box's label, each centred on its border, do not touch.
 */
const BOX_PAD = 16;
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
  // Every lane of the canvas is of one kind. A project lane leaves room for a
  // worktree box between its border and its nodes; its label needs no header,
  // because the gap above the first box holds it.
  const kind = graph.groups[0]?.kind;
  const boxed = kind === 'project';
  const pad = boxed ? LANE_INSET + BOX_PAD * 2 : LANE_PAD;
  const header = kind === 'none' || boxed ? 0 : LANE_HEADER;
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
    // A project lane stacks one section per worktree, each with rows of its
    // own. Every other lane is one section.
    const sections = new Map<Worktree | undefined, string[]>();
    for (const id of group.nodeIds) {
      const key = boxed ? worktreeOf.get(id) : undefined;
      const ids = sections.get(key);
      if (ids) ids.push(id);
      else sections.set(key, [id]);
    }
    const boxes: WorktreeBox[] = [];
    // A boxed lane starts one gap inside its border; its nodes are a second gap in.
    const edge = boxed ? LANE_INSET + BOX_PAD : pad;
    const top = header + edge;
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
      const rowsY = worktree ? y + BOX_PAD : y;
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
      y = bottom + BOX_PAD;
    }
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
