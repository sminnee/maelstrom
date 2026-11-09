import type { TaskStatus } from '../protocol/entities';
import type { DeskId, WorktreeId } from '../protocol/ids';
import { isLive, nodeWorktree, workDeskId, worktreeHolders, type GraphNode } from './graph';
import { canClose } from './worktrees';
import type { WorldView } from './world';

/**
 * Which nodes of a canvas **Selection** each bulk-bar action applies to. A
 * node an action does not fit is skipped, never refused: a free agent has no
 * status or comms, and a task already in the target state has nothing to write.
 */

/** The task nodes not yet in `status`. */
export function statusTargets(nodes: GraphNode[], status: TaskStatus): GraphNode[] {
  return nodes.filter((n) => n.task && n.task.status !== status);
}

/** The task nodes not yet linked to `commId`. */
export function linkTargets(nodes: GraphNode[], commId: string): GraphNode[] {
  return nodes.filter((n) => n.task && !n.task.comms.includes(commId));
}

/** The task nodes linked to `commId`. */
export function unlinkTargets(nodes: GraphNode[], commId: string): GraphNode[] {
  return nodes.filter((n) => n.task?.comms.includes(commId));
}

/** Every comm a selected task is linked to, once each, in first-seen order. */
export function linkedComms(nodes: GraphNode[]): string[] {
  return [...new Set(nodes.flatMap((n) => n.task?.comms ?? []))];
}

/** One node's part of a bulk **Dismiss**. */
export interface DismissStep {
  node: GraphNode;
  deskId: DeskId;
  chain: 'terminate' | 'offDesk' | 'close';
  /** The worktree a close ends. */
  worktreeId?: WorktreeId;
}

/** The desk id a node is filed under. */
const deskIdOf = (node: GraphNode): DeskId => workDeskId(node.agent, node.task?.id);

/**
 * Each node's Dismiss chain: the one a single Dismiss would run, with one
 * change. A worktree closes once, on the last selected node in it, so no close
 * races a sibling's terminate. A worktree with a **Holder** outside the
 * selection does not close at all. A node with nothing live and no desk entry is left out.
 */
export function planDismiss(world: WorldView, nodes: GraphNode[]): DismissStep[] {
  const selected = new Set(nodes.map(deskIdOf));
  const lastIn = new Map<WorktreeId, GraphNode>();
  for (const node of nodes) {
    const where = nodeWorktree(world, node);
    if (where && canClose(where)) lastIn.set(where.id, node);
  }
  const steps: DismissStep[] = [];
  for (const node of nodes) {
    const deskId = deskIdOf(node);
    const where = nodeWorktree(world, node);
    const closes =
      where &&
      lastIn.get(where.id) === node &&
      [...worktreeHolders(world, where).keys()].every((id) => selected.has(id));
    if (closes) steps.push({ node, deskId, chain: 'close', worktreeId: where.id });
    else if (isLive(node.agent)) steps.push({ node, deskId, chain: 'terminate' });
    else if (deskId in world.desk) steps.push({ node, deskId, chain: 'offDesk' });
  }
  return steps;
}
