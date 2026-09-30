import { useResume, useStop } from '../api/agents';
import { useTakeOffDesk } from '../api/desk';
import { useCloseWorktree } from '../api/worktrees';
import { useWorld } from '../api/useWorld';
import { deskIdForAgent, deskIdForTask } from '../protocol/deskId';
import type { Agent, Worktree } from '../protocol/entities';
import type { TaskId } from '../protocol/ids';
import { isLive } from '../selectors/graph';
import { canClose } from '../selectors/worktrees';
import { AppButton } from '../ui/AppButton';
import { SplitButton, type SplitOption } from '../ui/SplitButton';

/**
 * Resume, while the agent is not live, and the end-of-work control. The
 * expanded card and the session head both draw these.
 *
 * `taskId` names the desk entry a dismiss takes: a task node's own, else the
 * agent's task, else — for a free agent — the agent itself. A task with no
 * agent still dismisses, so `agent` is optional.
 *
 * `onTakenOffDesk` runs once a dismiss has taken the entry off the desk: the
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
  const task = taskId || agent?.taskId;
  const endOfWork = endOfWorkOptions({
    live: isLive(agent),
    where,
    others: where ? otherLiveAgents(world.agents, where.id, agent?.id) : 0,
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
  });
  return (
    <>
      {agent && !isLive(agent) && (
        <AppButton
          variant="quiet"
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
 * agent is live, Dismiss once it is not. A close runs first in its chain and
 * sends no stop — see `docs/dev/orchestrator-ui.md`.
 */
function endOfWorkOptions({
  live,
  where,
  others,
  stop,
  takeOffDesk,
  close,
}: {
  live: boolean;
  where: Worktree | undefined;
  /** Live agents in `where` other than this node's own. */
  others: number;
  stop: () => Promise<unknown>;
  takeOffDesk: () => Promise<unknown>;
  close: () => Promise<unknown>;
}): SplitOption[] {
  const options: SplitOption[] = live
    ? [
        { label: 'Terminate', processing: 'Terminating…', run: stop },
        {
          label: 'Terminate & dismiss',
          processing: 'Terminating…',
          run: async () => {
            await stop();
            await takeOffDesk();
          },
        },
      ]
    : [{ label: 'Dismiss', run: takeOffDesk }];
  if (where && canClose(where)) {
    options.push({
      label: `${live ? 'Terminate, dismiss' : 'Dismiss'} & close ${where.nato}`,
      processing: 'Closing…',
      disabled: others > 0,
      detail:
        others > 0
          ? `${others} other ${others === 1 ? 'agent' : 'agents'} still running in ${where.nato}`
          : undefined,
      run: async () => {
        await close();
        await takeOffDesk();
      },
    });
  }
  return options;
}

/**
 * Live top-level agents in a worktree, leaving out `self`. A subagent is not
 * counted: it runs inside its parent, and stops with it.
 */
function otherLiveAgents(
  agents: Record<string, Agent>,
  worktreeId: string,
  self: string | undefined,
): number {
  return Object.values(agents).filter(
    (a) => a.worktreeId === worktreeId && a.id !== self && !a.parent && isLive(a),
  ).length;
}
