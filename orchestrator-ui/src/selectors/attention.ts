import type { Attention, AttentionKind } from '../protocol/attention';
import type { GraphNode } from './graph';

const RANK: Partial<Record<AttentionKind, number>> = {
  plan_review: 0,
  document_review: 1,
  question: 2,
  permission: 3,
};
const rank = (kind: AttentionKind) => RANK[kind] ?? 4;
const byRank = (a: Attention, b: Attention) =>
  rank(a.kind) - rank(b.kind) || a.raisedAt.localeCompare(b.raisedAt);

/**
 * The nodes in state `needs-attention`, ordered by each node's best-ranked
 * open item, oldest first within a rank.
 */
export function attentionNodes(nodes: readonly GraphNode[]): GraphNode[] {
  return (
    nodes
      .filter((n) => n.progress.state === 'needs-attention')
      // A node is in this state only through an open item, so `best` exists.
      .map((node) => ({ node, best: [...node.attention].sort(byRank)[0]! }))
      .sort((a, b) => byRank(a.best, b.best))
      .map(({ node }) => node)
  );
}

/** The node the attention chip should take the user to next, cycling from `current`. */
export function nextAttentionNode(
  nodes: readonly GraphNode[],
  current: string | null,
): string | null {
  const ids = attentionNodes(nodes).map((n) => n.id);
  if (ids.length === 0) return null;
  const index = current ? ids.indexOf(current) : -1;
  return ids[(index + 1) % ids.length] ?? null;
}
