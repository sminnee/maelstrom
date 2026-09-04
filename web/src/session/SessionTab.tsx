import { useEffect, useRef } from 'react';
import { useAnswer, useApprove, useDeny, useSay } from '../api/agents';
import { useWorld } from '../api/useWorld';
import { describeState } from '../selectors/status';
import { answeredOnCanvas } from '../selectors/transcript';
import { useAppStore } from '../store/store';
import { MessageInput } from './MessageInput';
import { Transcript } from './Transcript';
import styles from './SessionTab.module.css';

/** The rich transcript plus an input. VS-Code-extension-like, not a terminal. */
export function SessionTab({ agentId }: { agentId: string }) {
  const approve = useApprove();
  const deny = useDeny();
  const answer = useAnswer();
  const say = useSay();
  const { world } = useWorld();
  const agent = world.agents[agentId];
  const task = agent ? world.tasks[agent.taskId] : undefined;
  const transcript = useAppStore((s) => s.transcripts[agentId]);
  const bottom = useRef<HTMLDivElement>(null);
  const count = transcript?.items.length ?? 0;
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
        {agent.waitingOn && <span className={styles.waiting}>{agent.waitingOn}</span>}
      </div>
      <div className={styles.scroll}>
        <Transcript
          items={transcript?.items ?? []}
          truncatedBefore={transcript?.truncatedBefore ?? false}
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
