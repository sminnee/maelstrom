import { useResume, useStop } from '../api/agents';
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
import { AppButton } from '../ui/AppButton';
import { trashConfirm } from '../worktrees/trashConfirm';
import { SplitButton, type SplitOption } from '../ui/SplitButton';

/**
 * Resume, while the agent is not live, and the end-of-work control. The
 * expanded card and the session head both draw these.
 *
 * `taskId` names the desk entry Off desk takes: a task node's own, else the
 * agent's task, else — for a free agent — the agent itself. A task with no
 * agent still goes off the desk, so `agent` is optional.
 *
 * `onTakenOffDesk` runs once Off desk has taken the entry off the desk: the
 * card collapses, and the panel closes the session tab.
 */
export function AgentControls({
  agent,
  taskId,
  where,
  onTakenOffDesk,
}: {
  agent: Agent | undefined;
  taskId?: TaskId;
  where: Worktree | undefined;
  onTakenOffDesk: () => void;
}) {
  const { world } = useWorld();
  const stop = useStop();
  const resume = useResume();
  const offDesk = useTakeOffDesk();
  const closeWorktree = useCloseWorktree();
  const trashWorktree = useTrashWorktree();
  const task = taskId || agent?.taskId;
  const endOfWork = endOfWorkOptions({
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
  return (
    <>
      {agent && !isLive(agent) && (
        <AppButton
          variant="quiet"
          icon={actionIcon('resume')}
          processingChildren="Resuming"
          onClick={() => resume.mutateAsync({ agentId: agent.id })}
        >
          Resume
        </AppButton>
      )}
      <SplitButton variant="quiet" options={endOfWork} />
    </>
  );
}

/**
 * The end-of-work control's options, the usual one first: Terminate while the
 * agent is live, Off desk once it is not. A close or a trash runs first in its
 * chain and sends no stop — see `docs/dev/orchestrator-ui.md`.
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
    : [{ label: 'Off desk', icon: <OffDeskIcon />, run: takeOffDesk }];
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
    options.push(
      {
        label: `${lead} & close ${where.nato}`,
        processing: 'Closing…',
        ...held,
        run: async () => {
          await close();
          await takeOffDesk();
        },
      },
      {
        label: `${lead} & trash ${where.nato}`,
        processing: 'Trashing…',
        ...held,
        confirm: trashConfirm(where),
        run: async () => {
          await trash();
          await takeOffDesk();
        },
      },
    );
  }
  return options;
}
