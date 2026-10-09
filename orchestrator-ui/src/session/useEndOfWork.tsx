import { useStop } from '../api/agents';
import { useTakeOffDesk } from '../api/desk';
import { useCloseWorktree, useTrashWorktree } from '../api/worktrees';
import { useWorld } from '../api/useWorld';
import { deskIdForAgent, deskIdForTask } from '../protocol/deskId';
import type { Agent, Worktree } from '../protocol/entities';
import type { TaskId } from '../protocol/ids';
import { isLive } from '../selectors/graph';
import { canClose, trackedAgents } from '../selectors/worktrees';
import { OffDeskIcon } from '../shell/OffDeskIcon';
import { actionIcon } from '../ui/actionIcons';
import type { SplitOption } from '../ui/SplitButton';
import { trashConfirm } from '../worktrees/trashConfirm';

export interface EndOfWorkProps {
  agent: Agent | undefined;
  taskId?: TaskId;
  where: Worktree | undefined;
  onTakenOffDesk: () => void;
}

/**
 * The end-of-work options for a node. The one with `isDefault` is Dismiss. The
 * card's split button and a deck row's swipe both run these, so the two cannot
 * drift on what Dismiss does.
 */
export function useEndOfWork(props: EndOfWorkProps): SplitOption[] {
  return useEndOfWorkLater(props)();
}

/**
 * `useEndOfWork`, built only when called. A deck row needs the options at the
 * moment of a swipe, not on every render of every row.
 */
export function useEndOfWorkLater({
  agent,
  taskId,
  where,
  onTakenOffDesk,
}: EndOfWorkProps): () => SplitOption[] {
  const { world } = useWorld();
  const stop = useStop();
  const offDesk = useTakeOffDesk();
  const closeWorktree = useCloseWorktree();
  const trashWorktree = useTrashWorktree();
  const task = taskId || agent?.taskId;
  return () =>
    endOfWorkOptions({
      live: isLive(agent),
      where,
      // The agents the worktree's own close control counts, less this node's.
      others: where ? trackedAgents(world, where.id).filter((a) => a.id !== agent?.id).length : 0,
      // Terminate ends the process; the session tab's Stop only abandons the
      // turn — see CONTEXT.md, "Interrupt".
      stop: () => stop.mutateAsync({ agentId: agent!.id }),
      takeOffDesk: async () => {
        const id = task ? deskIdForTask(task) : deskIdForAgent(agent!.id);
        // A live node draws with no desk entry, so there may be none to take.
        if (id in world.desk) await offDesk.mutateAsync({ id });
        onTakenOffDesk();
      },
      close: () => closeWorktree.mutateAsync({ worktreeId: where!.id }),
      trash: () => trashWorktree.mutateAsync({ worktreeId: where!.id }),
    });
}

/**
 * The end-of-work control's options. The default is the Dismiss chain — see
 * `CONTEXT.md`, "Dismiss". A close or a trash runs first in its chain and
 * sends no stop — see `docs/dev/orchestrator-ui.md`.
 */
function endOfWorkOptions({
  live,
  where,
  others,
  stop,
  takeOffDesk,
  close,
  trash,
}: {
  live: boolean;
  where: Worktree | undefined;
  /** Live agents in `where` other than this node's own. */
  others: number;
  stop: () => Promise<unknown>;
  takeOffDesk: () => Promise<unknown>;
  close: () => Promise<unknown>;
  trash: () => Promise<unknown>;
}): SplitOption[] {
  const options: SplitOption[] = live
    ? [
        {
          label: 'Terminate',
          icon: actionIcon('terminate'),
          processing: 'Terminating…',
          run: stop,
        },
        {
          label: 'Terminate & take off desk',
          icon: <OffDeskIcon />,
          processing: 'Terminating…',
          run: async () => {
            await stop();
            await takeOffDesk();
          },
        },
      ]
    : [
        {
          label: 'Off desk',
          icon: <OffDeskIcon />,
          processing: 'Taking off desk…',
          run: takeOffDesk,
        },
      ];
  // Dismiss runs the close chain when it can, else the widest chain that keeps the worktree.
  let dismiss = options.at(-1)!;
  if (where && canClose(where)) {
    const lead = live ? 'Terminate, take off desk' : 'Take off desk';
    const held = {
      icon: <OffDeskIcon />,
      disabled: others > 0,
      detail:
        others > 0
          ? `${others} other ${others === 1 ? 'agent' : 'agents'} still running in ${where.nato}`
          : undefined,
    };
    const closeChain: SplitOption = {
      label: `${lead} & close ${where.nato}`,
      processing: 'Closing…',
      ...held,
      run: async () => {
        await close();
        await takeOffDesk();
      },
    };
    if (!closeChain.disabled) dismiss = closeChain;
    options.push(closeChain, {
      label: `${lead} & trash ${where.nato}`,
      processing: 'Trashing…',
      ...held,
      confirm: trashConfirm(where),
      run: async () => {
        await trash();
        await takeOffDesk();
      },
    });
  }
  return options.map((o) =>
    o === dismiss ? { ...o, isDefault: true, buttonLabel: 'Dismiss' } : o,
  );
}
