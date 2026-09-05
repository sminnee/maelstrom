import { describe, expect, it } from 'vitest';
import { layoutSwimlanes } from './layout';
import { deriveGraph } from '../selectors/graph';
import { noFilters } from '../selectors/filters';
import { makeAgent, makeTask, onDesk, worldWith } from '../test/fixtures';
import type { Agent, Task } from '../protocol/entities';

function graphOf(tasks: Task[], agents: Agent[] = []) {
  return deriveGraph(worldWith({ tasks, agents, desk: onDesk(tasks) }), {
    groupBy: 'project',
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

  it('group by none lays the whole world out as one band with no header', () => {
    const graph = deriveGraph(worldWith({ tasks: chain, desk: onDesk(chain) }), {
      groupBy: 'none',
      filters: noFilters(),
    });
    const layout = layoutSwimlanes(graph);
    expect(Object.keys(layout.groups)).toEqual(['all']);
    expect(Math.min(...graph.nodes.map((n) => layout.nodes[n.id]!.y))).toBeLessThan(
      layoutSwimlanes(graphOf(chain)).nodes['A']!.y,
    );
    expect(layout.nodes['B']!.x).toBeGreaterThan(layout.nodes['A']!.x);
    expect(layout.nodes['F']!.x).toBeGreaterThan(layout.nodes['E']!.x);
    const boxes = graph.nodes.map((n) => ({ ...layout.nodes[n.id]!, ...layout.nodeSize }));
    for (let i = 0; i < boxes.length; i += 1) {
      for (let j = i + 1; j < boxes.length; j += 1) {
        expect(overlaps(boxes[i]!, boxes[j]!)).toBe(false);
      }
    }
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
