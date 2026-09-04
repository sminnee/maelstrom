import { useEffect, useRef } from 'react';
import { useAnswer, useApprove, useDeny, useSay, useSetMode } from '../api/agents';
import { useWorld } from '../api/useWorld';
import { useAgentStream } from '../live/useAgentStream';
import { nextMode } from '../protocol/modes';
import { describeState } from '../selectors/status';
import { answeredOnCanvas } from '../selectors/transcript';
import { useAppStore } from '../store/store';
import { AppButton } from '../ui/AppButton';
import { MessageInput } from './MessageInput';
import { Transcript } from './Transcript';
import styles from './SessionTab.module.css';

/** The rich transcript plus an input. VS-Code-extension-like, not a terminal. */
export function SessionTab({ agentId }: { agentId: string }) {
  const approve = useApprove();
  const deny = useDeny();
  const answer = useAnswer();
  const say = useSay();
  const setMode = useSetMode();
  const { world } = useWorld();
  const agent = world.agents[agentId];
  const task = agent ? world.tasks[agent.taskId] : undefined;
  const transcript = useAgentStream(agentId);
  const bottom = useRef<HTMLDivElement>(null);
  const count = transcript.items.length;
  const expandedNodeId = useAppStore((s) => s.ui.expandedNodeId);
  // A free agent draws under its own id, a task node under its task's.
  const deferred =
    agent?.pendingRequestId != null && answeredOnCanvas(expandedNodeId, agent.taskId || agent.id);

  useEffect(() => {
    bottom.current?.scrollIntoView?.({ block: 'end' });
  }, [count]);

  if (!agent) return <div className={styles.empty}>Agent {agentId} is gone.</div>;
  return (
    <div className={styles.session}>
      <div className={styles.head}>
        <span className={styles.agent}>{agent.id}</span>
        <span className={styles.state} data-state={agent.state}>
          {describeState(task, agent)}
        </span>
        {agent.permissionMode && (
          <AppButton
            variant="quiet"
            className={styles.mode}
            title={`Permission mode: ${agent.permissionMode}. Click for ${nextMode(agent.permissionMode)}.`}
            onClick={() => setMode.mutateAsync({ agentId, mode: nextMode(agent.permissionMode) })}
          >
            {agent.permissionMode}
          </AppButton>
        )}
        {agent.waitingOn && <span className={styles.waiting}>{agent.waitingOn}</span>}
      </div>
      <div className={styles.scroll}>
        {transcript.status === 'connecting' && count === 0 && (
          <div className={styles.empty}>Loading the transcript…</div>
        )}
        {transcript.status === 'reconnecting' && (
          <div className={styles.empty} role="status">
            Reconnecting to the transcript…
          </div>
        )}
        {transcript.status === 'ended' && (
          <div className={styles.empty} role="status">
            The server no longer knows this agent.
          </div>
        )}
        <Transcript
          items={transcript.items}
          truncatedBefore={transcript.truncatedBefore}
          deferredRequestId={deferred ? agent.pendingRequestId : null}
          handlers={{
            onAnswer: (requestId, answers) => answer.mutateAsync({ agentId, requestId, answers }),
            onDecide: (requestId, decision, reason) =>
              decision === 'approve'
                ? approve.mutateAsync({ agentId, requestId })
                : deny.mutateAsync({ agentId, requestId, reason }),
          }}
        />
        <div ref={bottom} />
      </div>
      <MessageInput
        disabled={agent.state === 'exited'}
        onSend={(text) => say.mutateAsync({ agentId, text })}
      />
    </div>
  );
}
