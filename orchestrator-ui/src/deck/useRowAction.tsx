import type { ReactNode } from 'react';
import { useLaunch } from '../api/tasks';
import { useWorld } from '../api/useWorld';
import { type NodeState, zoneForState } from '../protocol/progress';
import type { GraphNode } from '../selectors/graph';
import { nodeWorktree } from '../selectors/graph';
import { useEndOfWorkLater } from '../session/useEndOfWork';
import { OffDeskIcon } from '../shell/OffDeskIcon';
import { actionIcon } from '../ui/actionIcons';

/** What a deck row's left swipe does. See `CONTEXT.md`, "Swipe action". */
export interface RowAction {
  kind: 'launch' | 'dismiss';
  label: string;
  icon: ReactNode;
  run: () => Promise<unknown>;
}

/** The states whose work has ended, so a swipe dismisses the row. */
const ENDED: readonly NodeState[] = ['done', 'cancelled', 'stopped', 'exited'];

/**
 * A row's swipe action: Launch on a not-started task that can start, Dismiss
 * on work that has ended, else none.
 */
export function useRowAction(node: GraphNode): RowAction | null {
  const { world } = useWorld();
  const launch = useLaunch();
  const endOfWork = useEndOfWorkLater({
    agent: node.agent,
    taskId: node.task?.id,
    where: nodeWorktree(world, node),
    // The row leaves the deck when the world changes; there is nothing to close.
    onTakenOffDesk: () => {},
  });
  const { task } = node;
  const { state } = node.progress;
  if (!node.agent && task?.actionable && zoneForState(state) === 'notStarted')
    return {
      kind: 'launch',
      label: 'Launch',
      icon: actionIcon('launch'),
      run: () => launch.mutateAsync({ taskId: task.id }),
    };
  if (ENDED.includes(state))
    return {
      kind: 'dismiss',
      label: 'Dismiss',
      icon: <OffDeskIcon />,
      // There is always a default: the widest chain that keeps the worktree.
      run: () =>
        endOfWork()
          .find((o) => o.isDefault)!
          .run(),
    };
  return null;
}
