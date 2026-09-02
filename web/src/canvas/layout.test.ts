import { describe, expect, it } from 'vitest';
import { layoutSwimlanes } from './layout';
import { deriveGraph } from '../selectors/graph';
import { noFilters } from '../selectors/filters';
import { makeTask, worldWith } from '../test/fixtures';
import type { Task } from '../protocol/entities';

function graphOf(tasks: Task[]) {
  return deriveGraph(worldWith({ tasks }), { groupBy: 'project', filters: noFilters() });
}

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
    // C has no predecessor in its own band, so it takes column 0 and the first row.
    expect(layout.nodes['C']).toEqual(layout.nodes['A']);
    expect(layout.nodes['D']!.y).toBeGreaterThan(layout.nodes['C']!.y);
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
});
