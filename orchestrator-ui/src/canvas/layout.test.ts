import { describe, expect, it } from 'vitest';
import { layoutSwimlanes } from './layout';
import { deriveGraph } from '../selectors/graph';
import { noFilters } from '../selectors/filters';
import { makeAgent, makeTask, makeWorktree, onDesk, worldWith } from '../test/fixtures';
import type { Agent, Task } from '../protocol/entities';

function graphOf(tasks: Task[], agents: Agent[] = []) {
  return deriveGraph(worldWith({ tasks, agents, desk: onDesk(tasks) }), {
    filters: noFilters(),
  });
}

/** A task in each of the three zones, without an edge to argue about. */
const doneTask = (id: string, project: string, follows: string[] = []) =>
  makeTask({ id, project, follows, status: 'done', actionable: false });
const runningTask = (id: string, project: string, follows: string[] = []) =>
  makeTask({ id, project, follows, status: 'in-progress', actionable: false });
const agentOn = (id: string, taskId: string) => makeAgent({ id, taskId, state: 'processing' });

const chain = [
  makeTask({ id: 'A', project: 'p1' }),
  makeTask({ id: 'B', project: 'p1', follows: ['A'] }),
  makeTask({ id: 'C', project: 'p1', follows: ['B'] }),
  makeTask({ id: 'D', project: 'p1' }),
  makeTask({ id: 'E', project: 'p2' }),
  makeTask({ id: 'F', project: 'p2', follows: ['E'] }),
];

function overlaps(
  a: { x: number; y: number; width: number; height: number },
  b: { x: number; y: number; width: number; height: number },
) {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
}

describe('layoutSwimlanes', () => {
  it('puts every node inside its own group band and bands do not overlap', () => {
    const graph = graphOf(chain);
    const layout = layoutSwimlanes(graph);
    for (const node of graph.nodes) {
      const pos = layout.nodes[node.id]!;
      const band = layout.groups[node.groupId]!;
      expect(pos.x).toBeGreaterThanOrEqual(0);
      expect(pos.y).toBeGreaterThanOrEqual(0);
      expect(pos.x + layout.nodeSize.width).toBeLessThanOrEqual(band.width);
      expect(pos.y + layout.nodeSize.height).toBeLessThanOrEqual(band.height);
    }
    const bands = Object.values(layout.groups);
    for (let i = 0; i < bands.length; i += 1) {
      for (let j = i + 1; j < bands.length; j += 1) {
        expect(overlaps(bands[i]!, bands[j]!)).toBe(false);
      }
    }
  });

  it('a follower sits to the right of what it follows', () => {
    const layout = layoutSwimlanes(graphOf(chain));
    expect(layout.nodes['B']!.x).toBeGreaterThan(layout.nodes['A']!.x);
    expect(layout.nodes['C']!.x).toBeGreaterThan(layout.nodes['B']!.x);
    expect(layout.nodes['F']!.x).toBeGreaterThan(layout.nodes['E']!.x);
  });

  it('nodes in one group never overlap', () => {
    const graph = graphOf(chain);
    const layout = layoutSwimlanes(graph);
    const boxes = graph.nodes.map((n) => ({
      ...layout.nodes[n.id]!,
      ...layout.nodeSize,
      g: n.groupId,
    }));
    for (let i = 0; i < boxes.length; i += 1) {
      for (let j = i + 1; j < boxes.length; j += 1) {
        if (boxes[i]!.g !== boxes[j]!.g) continue;
        expect(overlaps(boxes[i]!, boxes[j]!)).toBe(false);
      }
    }
  });

  it('a follows edge across groups moves nothing in the follower band', () => {
    const across = [
      makeTask({ id: 'A', project: 'p1' }),
      makeTask({ id: 'B', project: 'p1', follows: ['A'] }),
      makeTask({ id: 'C', project: 'p2', follows: ['B'] }),
      makeTask({ id: 'D', project: 'p2' }),
    ];
    const layout = layoutSwimlanes(graphOf(across));
    // C has no predecessor in its own band, so it takes the first column of
    // the not-started zone, and the first row.
    expect(layout.nodes['C']).toEqual(layout.nodes['A']);
    expect(layout.nodes['D']!.y).toBeGreaterThan(layout.nodes['C']!.y);
  });

  it('puts a not-started follower on the row of the done task it follows', () => {
    const layout = layoutSwimlanes(
      graphOf([
        makeTask({ id: 'S1', project: 'p1' }),
        makeTask({ id: 'S2', project: 'p1' }),
        makeTask({ id: 'S3', project: 'p1' }),
        doneTask('T', 'p1'),
        makeTask({ id: 'U', project: 'p1', follows: ['T'] }),
      ]),
    );
    expect(layout.nodes['U']!.y).toBe(layout.nodes['T']!.y);
    expect(layout.nodes['U']!.x).toBe(layout.nodes['S1']!.x);
    expect(layout.nodes['T']!.x).toBeLessThan(layout.nodes['U']!.x);
  });

  it('is deterministic', () => {
    expect(layoutSwimlanes(graphOf(chain))).toEqual(layoutSwimlanes(graphOf(chain)));
  });

  it('adding a node keeps the existing rows and their relative order', () => {
    const before = layoutSwimlanes(graphOf(chain));
    const after = layoutSwimlanes(
      graphOf([...chain, makeTask({ id: 'G', project: 'p1', follows: ['A'] })]),
    );
    const groupOrderBefore = Object.keys(before.groups);
    const groupOrderAfter = Object.keys(after.groups);
    expect(groupOrderAfter).toEqual(groupOrderBefore);
    for (const id of ['A', 'B', 'C', 'D']) {
      expect(after.nodes[id]).toEqual(before.nodes[id]);
    }
    expect(after.nodes['G']!.x).toBeGreaterThan(after.nodes['A']!.x);
  });

  it('lines the zone boundaries up across every lane', () => {
    const layout = layoutSwimlanes(
      graphOf(
        [
          doneTask('A', 'p1'),
          doneTask('B', 'p1', ['A']),
          runningTask('C', 'p1'),
          runningTask('D', 'p2'),
        ],
        [agentOn('ag-c', 'C'), agentOn('ag-d', 'D')],
      ),
    );
    // p2 has no done history, so its running node starts where p1's does
    // rather than in p2's own first column.
    expect(layout.nodes['D']!.x).toBe(layout.nodes['C']!.x);
    // The done zone holds two columns: it spans from A's left edge to B's right.
    const done = layout.zones.find((z) => z.zone === 'done')!;
    expect(done.x).toBe(layout.nodes['A']!.x);
    expect(done.x + done.width).toBe(layout.nodes['B']!.x + layout.nodeSize.width);
  });

  it('leaves the done columns blank in a lane with no done task', () => {
    const layout = layoutSwimlanes(
      graphOf(
        [doneTask('A', 'p1'), runningTask('B', 'p1'), runningTask('C', 'p2')],
        [agentOn('ag-b', 'B'), agentOn('ag-c', 'C')],
      ),
    );
    expect(layout.nodes['C']!.x).toBeGreaterThan(layout.nodes['A']!.x);
  });

  it('reports all three zones in board order, with a zero-width empty zone', () => {
    const layout = layoutSwimlanes(
      graphOf([doneTask('A', 'p1'), makeTask({ id: 'B', project: 'p1' })]),
    );
    expect(layout.zones.map((z) => z.zone)).toEqual(['done', 'running', 'notStarted']);
    expect(layout.zones.map((z) => z.x)).toEqual(
      [...layout.zones.map((z) => z.x)].sort((a, b) => a - b),
    );
    expect(layout.zones.find((z) => z.zone === 'running')?.columns).toBe(0);
    expect(layout.zones.find((z) => z.zone === 'done')?.columns).toBe(1);
    // A zone spans its nodes and no more: each has one column here, so it
    // starts and ends with its one node. An empty zone has no width.
    const band = (zone: string) => layout.zones.find((z) => z.zone === zone)!;
    for (const [id, zone] of [
      ['A', 'done'],
      ['B', 'notStarted'],
    ] as const) {
      expect(band(zone).x).toBe(layout.nodes[id]!.x);
      expect(band(zone).width).toBe(layout.nodeSize.width);
    }
    expect(band('running').width).toBe(0);
  });

  it('puts a done task left of a running one with no edge between them', () => {
    const layout = layoutSwimlanes(
      graphOf([doneTask('A', 'p1'), runningTask('B', 'p1')], [agentOn('ag-b', 'B')]),
    );
    expect(layout.nodes['A']!.x).toBeLessThan(layout.nodes['B']!.x);
  });

  // The conflict case: progress wins, so the follower draws left of its head.
  it('keeps a done follower left of the running task it follows', () => {
    const layout = layoutSwimlanes(
      graphOf([runningTask('B', 'p1'), doneTask('A', 'p1', ['B'])], [agentOn('ag-b', 'B')]),
    );
    expect(layout.nodes['A']!.x).toBeLessThan(layout.nodes['B']!.x);
  });

  it('moves a node that starts one zone and nothing else', () => {
    const queued = [makeTask({ id: 'A', project: 'p1' }), makeTask({ id: 'B', project: 'p2' })];
    const before = layoutSwimlanes(graphOf(queued));
    const after = layoutSwimlanes(
      graphOf([runningTask('A', 'p1'), queued[1]!], [agentOn('ag-a', 'A')]),
    );
    // The lane widens by the column the running zone gained; the lanes
    // themselves keep their order and their vertical place.
    expect(Object.keys(after.groups)).toEqual(Object.keys(before.groups));
    for (const id of Object.keys(before.groups)) {
      expect(after.groups[id]!.y).toBe(before.groups[id]!.y);
      expect(after.groups[id]!.height).toBe(before.groups[id]!.height);
    }
    expect(after.nodes['A']!.x).toBeLessThan(after.nodes['B']!.x);
    expect(after.nodes['A']!.y).toBe(before.nodes['A']!.y);
    expect(after.nodes['B']!.y).toBe(before.nodes['B']!.y);
  });
});

describe('worktree boxes in a project lane', () => {
  const open = (nato: string, branch: string) =>
    makeWorktree({ id: `p1-${nato}`, project: 'p1', nato, branch });
  // Task ids sort in creation order. bravo holds the oldest task, so it leads
  // though alpha sorts first by name. T2 is on a branch no worktree holds, and
  // is older than every task of alpha. T3 follows T4 across two worktrees, so
  // the lane has a second not-started column that only a lane-wide count finds.
  const tasks = [
    doneTask('T1', 'p1'),
    makeTask({ id: 'T2', project: 'p1', branch: 'none' }),
    makeTask({ id: 'T3', project: 'p1', branch: 'a', follows: ['T4'] }),
    makeTask({ id: 'T4', project: 'p1', branch: 'b', follows: ['T1'] }),
  ].map((t) => (t.id === 'T1' ? { ...t, branch: 'b' } : t));
  const ids = tasks.map((t) => t.id);
  const held = [open('alpha', 'a'), open('bravo', 'b')];
  const emptyNames = ['charlie', 'delta', 'echo', 'foxtrot', 'golf', 'hotel', 'india', 'juliett'];
  const empty = emptyNames.map((n) => open(n, `empty-${n}`));

  function laidOut(
    worktrees = [...held, ...empty],
    agents: Agent[] = [],
    laneTasks: Task[] = tasks,
  ) {
    const ids = laneTasks.map((t) => t.id);
    const graph = deriveGraph(
      worldWith({ tasks: laneTasks, worktrees, agents, desk: onDesk(laneTasks) }),
      { filters: noFilters() },
    );
    const layout = layoutSwimlanes(graph);
    const cell = (id: string) => ({ ...layout.nodes[id]!, ...layout.nodeSize });
    const boxes = layout.worktreeBoxes['p1'] ?? [];
    const boxOf = (nato: string) => boxes.find((b) => b.worktree.nato === nato)!;
    const inside = (nato: string) => ids.filter((id) => overlaps(boxOf(nato), cell(id)));
    return { layout, cell, boxes, boxOf, inside };
  }

  const contains = (
    outer: { x: number; y: number; width: number; height: number },
    inner: { x: number; y: number; width: number; height: number },
  ) =>
    outer.x <= inner.x &&
    outer.y <= inner.y &&
    inner.x + inner.width <= outer.x + outer.width &&
    inner.y + inner.height <= outer.y + outer.height;

  it('puts two boxes in disjoint columns side by side, on one row', () => {
    const { cell, boxOf } = laidOut();
    const y = (id: string) => cell(id).y;
    // bravo holds columns 0 and 1, and alpha holds column 2.
    expect(y('T4')).toBe(y('T1'));
    expect(y('T3')).toBe(y('T4'));
    expect(boxOf('alpha').y).toBe(boxOf('bravo').y);
    // T2 has no box. Its column is in bravo, so it sits below bravo.
    expect(y('T2')).toBeGreaterThan(y('T1'));
  });

  it('draws a box round the nodes of each worktree and round no other node', () => {
    const { cell, boxes, boxOf, inside } = laidOut();
    expect(boxes.filter((b) => !b.empty).map((b) => b.worktree.nato)).toEqual(['bravo', 'alpha']);
    expect(inside('bravo')).toEqual(['T1', 'T4']);
    expect(inside('alpha')).toEqual(['T3']);
    // T1 and T4 are in different columns, so the box spans both.
    expect(cell('T4').x).toBeGreaterThan(cell('T1').x);
    for (const id of ['T1', 'T4']) expect(contains(boxOf('bravo'), cell(id))).toBe(true);
    expect(contains(boxOf('alpha'), cell('T3'))).toBe(true);
  });

  it('leaves one gap from the lane border to a box, and from the box to its nodes', () => {
    const { layout, cell, boxOf } = laidOut();
    // React Flow pads a group node by 10px, so the lane's border draws there.
    const inset = 10;
    const box = boxOf('bravo');
    const gap = cell('T1').x - box.x;
    expect(gap).toBeGreaterThan(0);
    expect(cell('T1').y - box.y).toBe(gap);
    expect(box.x + box.width - (cell('T4').x + cell('T4').width)).toBe(gap);
    expect(box.y + box.height - (cell('T1').y + cell('T1').height)).toBe(gap);
    // bravo is the first box of the lane, so it meets the lane border on two sides.
    expect(box.x - inset).toBe(gap);
    expect(box.y - inset).toBe(gap);
    // alpha is the last box, in the last column.
    const last = boxOf('alpha');
    const lane = layout.groups['p1']!;
    expect(lane.width - inset - (last.x + last.width)).toBe(gap);
    expect(last.y - inset).toBe(gap);
    // T2 has no box and sits one gap below bravo.
    expect(cell('T2').y - (box.y + box.height)).toBe(gap);
    // The lane ends one gap below what is lowest: the strip here, T2 with no strip.
    const bottom = (b: { y: number; height: number }) => b.y + b.height;
    const strip = laidOut().boxes.filter((b) => b.empty);
    expect(lane.height - inset - Math.max(...strip.map(bottom))).toBe(gap);
    const bare = laidOut(held);
    expect(bare.layout.groups['p1']!.height - inset - bottom(bare.cell('T2'))).toBe(gap);
  });

  it('leaves one gap between two boxes in one column', () => {
    const stacked = [
      makeTask({ id: 'U1', project: 'p1', branch: 'a' }),
      makeTask({ id: 'U2', project: 'p1', branch: 'b' }),
    ];
    const { layout, cell, boxOf } = laidOut(held, [], stacked);
    const [upper, lower] = [boxOf('alpha'), boxOf('bravo')];
    const gap = cell('U1').y - upper.y;
    expect(upper.x).toBe(lower.x);
    expect(lower.y - (upper.y + upper.height)).toBe(gap);
    // The lane ends one gap below its last box.
    const inset = 10;
    expect(layout.groups['p1']!.height - inset - (lower.y + lower.height)).toBe(gap);
  });

  // No edge joins V1 and V3, and V2 is between them in age. Without the box,
  // V2 takes the row between them.
  it('keeps a node with no box out of a box of two rows', () => {
    const column = [
      makeTask({ id: 'V1', project: 'p1', branch: 'a' }),
      makeTask({ id: 'V2', project: 'p1', branch: 'none' }),
      makeTask({ id: 'V3', project: 'p1', branch: 'a' }),
      makeTask({ id: 'V4', project: 'p1', branch: 'none' }),
    ];
    const { layout, cell, boxOf, inside } = laidOut(held, [], column);
    const y = (id: string) => cell(id).y;
    expect(inside('alpha')).toEqual(['V1', 'V3']);
    expect(y('V2')).toBeGreaterThan(y('V3'));
    // Inside the box and between two nodes with no box, the rows are the same distance apart.
    const pitch = y('V4') - y('V2');
    expect(pitch).toBeLessThan(y('V2') - y('V3'));
    expect(y('V3') - y('V1')).toBe(pitch);
    const pad = y('V1') - boxOf('alpha').y;
    expect(boxOf('alpha').height).toBe(pitch + layout.nodeSize.height + pad * 2);
  });

  it('puts a node with no worktree in no box', () => {
    const { cell, boxes } = laidOut();
    expect(boxes.filter((b) => overlaps(b, cell('T2')))).toEqual([]);
  });

  // The card of a stopped agent still names the worktree it ran in.
  it('draws a box for a closed worktree that a node names', () => {
    const closed = { ...open('zulu', ''), isClosed: true };
    const agent = makeAgent({ id: 'ag', taskId: 'T2', worktreeId: 'p1-zulu', state: 'exited' });
    const { boxOf, inside } = laidOut([...held, ...empty, closed], [agent]);
    expect(boxOf('zulu').empty).toBe(false);
    expect(inside('zulu')).toEqual(['T2']);
  });

  it('keeps every box inside its lane, and no two boxes overlap', () => {
    const { layout, boxes } = laidOut();
    expect(boxes).toHaveLength(held.length + empty.length);
    const lane = { ...layout.groups['p1']!, x: 0, y: 0 };
    for (const box of boxes) expect(contains(lane, box)).toBe(true);
    for (let i = 0; i < boxes.length; i += 1) {
      for (let j = i + 1; j < boxes.length; j += 1) {
        expect(overlaps(boxes[i]!, boxes[j]!)).toBe(false);
      }
    }
  });

  it('puts the empty boxes below every node, on more than one line', () => {
    const { cell, boxes } = laidOut();
    const strip = boxes.filter((b) => b.empty);
    expect(strip.map((b) => b.worktree.nato)).toEqual(emptyNames);
    const lowest = Math.max(...ids.map((id) => cell(id).y + cell(id).height));
    for (const box of strip) expect(box.y).toBeGreaterThanOrEqual(lowest);
    // Three columns of nodes make a lane too narrow for eight boxes in a line.
    expect(new Set(strip.map((b) => b.y)).size).toBeGreaterThan(1);
  });

  it('starts each empty box where a box that holds a node in that column starts', () => {
    const { boxes, boxOf } = laidOut();
    // bravo starts at column 0 and alpha at column 2; one column is 276 wide.
    const starts = [boxOf('bravo').x, boxOf('bravo').x + 276, boxOf('alpha').x];
    const strip = boxes.filter((b) => b.empty);
    expect(strip.map((b) => b.x)).toEqual([...starts, ...starts, ...starts.slice(0, 2)]);
  });

  it('moves no node sideways', () => {
    const boxed = laidOut();
    const plain = laidOut([]);
    expect(plain.boxes).toEqual([]);
    for (const id of ids) expect(boxed.cell(id).x).toBe(plain.cell(id).x);
    // T3 follows T4 from another worktree, and still sits one column right of it.
    expect(boxed.cell('T3').x).toBeGreaterThan(boxed.cell('T4').x);
    expect(boxed.layout.boardWidth).toBe(plain.layout.boardWidth);
  });
});
