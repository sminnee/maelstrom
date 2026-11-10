import { useAgents, useResume } from '../api/agents';
import { useTasks } from '../api/tasks';
import { isLive, startsNewSession } from '../selectors/graph';
import { actionIcon } from '../ui/actionIcons';
import { SplitButton } from '../ui/SplitButton';
import { type EndOfWorkProps, useEndOfWork } from './useEndOfWork';
import { useLaunchOptions } from './useLaunchOptions';

/**
 * Resume, while the agent is not live, and the end-of-work control. A task's
 * agent that is not live also offers a **New session** in the Resume menu. The
 * expanded card and the session head both draw these.
 *
 * `taskId` names the desk entry Off desk takes: a task node's own, else the
 * agent's task, else — for a free agent — the agent itself. A task with no
 * agent still goes off the desk, so `agent` is optional.
 *
 * `onTakenOffDesk` runs once Off desk has taken the entry off the desk, or once
 * a Dismiss starts: the card collapses, and the panel closes the session tab.
 */
export function AgentControls({ agent, taskId, where, onTakenOffDesk }: EndOfWorkProps) {
  const resume = useResume();
  const endOfWork = useEndOfWork({ agent, taskId, where, onTakenOffDesk });
  const agents = useAgents().data?.agents ?? [];
  const tasks = useTasks().data?.tasks ?? [];
  const task = tasks.find((t) => t.id === agent?.taskId);
  const tasksById = Object.fromEntries(tasks.map((t) => [t.id, t]));
  const opens = task !== undefined && startsNewSession(task, tasksById, agents);
  // A task's agent can also start a **New session**: in the menu, never the
  // main segment, which stays Resume.
  const newSession = useLaunchOptions(opens ? task : undefined, {
    newSession: true,
  }).map((option) => ({ ...option, isDefault: false }));
  return (
    <>
      {agent && !isLive(agent) && (
        <SplitButton
          variant="quiet"
          menuLabel="More resume actions"
          options={[
            {
              label: 'Resume',
              icon: actionIcon('resume'),
              processing: 'Resuming',
              run: () => resume.mutateAsync({ agentId: agent.id }),
            },
            {
              label: 'Resume and compact',
              processing: 'Resuming',
              run: () => resume.mutateAsync({ agentId: agent.id, compact: true }),
            },
            ...newSession,
          ]}
        />
      )}
      <SplitButton variant="quiet" options={endOfWork} />
    </>
  );
}
