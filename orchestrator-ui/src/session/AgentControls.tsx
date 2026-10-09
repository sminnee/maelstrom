import { useResume } from '../api/agents';
import { isLive } from '../selectors/graph';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { SplitButton } from '../ui/SplitButton';
import { type EndOfWorkProps, useEndOfWork } from './useEndOfWork';

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
export function AgentControls({ agent, taskId, where, onTakenOffDesk }: EndOfWorkProps) {
  const resume = useResume();
  const endOfWork = useEndOfWork({ agent, taskId, where, onTakenOffDesk });
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
