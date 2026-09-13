import { describe, expect, it } from 'vitest';
import { reduceEdges, type ReduceEdge, type ReduceNode } from './reduce';

/** A node the reducer can read: an id and the status that decides gating. */
function n(id: string, status: ReduceNode['status'] = 'todo'): ReduceNode {
  return { id, status };
}

/** An edge in the reducer's own shape: followed -> follower. */
function e(source: string, target: string): ReduceEdge {
  return { id: `${source}->${target}`, source, target };
}

/** The ids the reducer keeps, in input order, for a readable assertion. */
function kept(edges: ReduceEdge[], nodes: ReduceNode[]): string[] {
  return reduceEdges(edges, nodes).map((edge: ReduceEdge) => edge.id);
}

describe('reduceEdges', () => {
  it('keeps an edge with nothing to imply it', () => {
    expect(kept([e('A', 'B')], [n('A'), n('B')])).toEqual(['A->B']);
  });

  // The user's case. C follows both A and B; B follows A and is incomplete,
  // so B already gates C and the long wire says nothing the short ones do not.
  it('hides an edge a chain of incomplete tasks already implies', () => {
    const edges = [e('A', 'B'), e('B', 'C'), e('A', 'C')];
    expect(kept(edges, [n('A'), n('B'), n('C')])).toEqual(['A->B', 'B->C']);
  });

  // `is_actionable` in task.py gates on done alone, so a done intermediate
  // holds nothing up and the direct edge is the only one still gating.
  it('keeps the edge when the intermediate is done', () => {
    const edges = [e('A', 'B'), e('B', 'C'), e('A', 'C')];
    expect(kept(edges, [n('A'), n('B', 'done'), n('C')])).toEqual(['A->B', 'B->C', 'A->C']);
  });

  it('brings the edge back when the intermediate finishes', () => {
    const edges = [e('A', 'B'), e('B', 'C'), e('A', 'C')];
    expect(kept(edges, [n('A'), n('B'), n('C')])).not.toContain('A->C');
    expect(kept(edges, [n('A'), n('B', 'done'), n('C')])).toContain('A->C');
  });

  // Cancelled is not done, so it still gates — the `is_actionable` mirror,
  // and an open question in CONTEXT.md rather than a settled rule.
  it('hides through a cancelled intermediate, which still gates', () => {
    const edges = [e('A', 'B'), e('B', 'C'), e('A', 'C')];
    expect(kept(edges, [n('A'), n('B', 'cancelled'), n('C')])).toEqual(['A->B', 'B->C']);
  });

  // A node standing for no task has no status, so it gates like unfinished work.
  it('treats a node with no task as still gating', () => {
    const edges = [e('A', 'B'), e('B', 'C'), e('A', 'C')];
    expect(kept(edges, [n('A'), n('B', undefined), n('C')])).toEqual(['A->B', 'B->C']);
  });

  it('keeps every edge out of a done head', () => {
    const edges = [e('A', 'B'), e('B', 'C'), e('A', 'C')];
    expect(kept(edges, [n('A', 'done'), n('B'), n('C')])).toEqual(['A->B', 'B->C', 'A->C']);
  });

  it('reduces through a longer chain of incomplete tasks', () => {
    const edges = [e('A', 'B'), e('B', 'C'), e('C', 'D'), e('A', 'D')];
    expect(kept(edges, [n('A'), n('B'), n('C'), n('D')])).toEqual(['A->B', 'B->C', 'C->D']);
  });

  // A diamond implies nothing: neither branch is a path through the other.
  it('keeps both sides of a diamond', () => {
    const edges = [e('A', 'B'), e('A', 'C'), e('B', 'D'), e('C', 'D')];
    const nodes = [n('A'), n('B'), n('C'), n('D')];
    expect(kept(edges, nodes)).toEqual(['A->B', 'A->C', 'B->D', 'C->D']);
  });

  // Both edges stand: neither is implied by a path that avoids it. Asserting
  // the answer, not just that nothing threw, pins the `seen` guard.
  it('terminates on a cycle and keeps both edges', () => {
    const edges = [e('A', 'B'), e('B', 'A')];
    expect(kept(edges, [n('A'), n('B')])).toEqual(['A->B', 'B->A']);
  });

  it('leaves an edge whose endpoints are not in the node list', () => {
    expect(kept([e('A', 'ghost')], [n('A')])).toEqual(['A->ghost']);
  });
});
