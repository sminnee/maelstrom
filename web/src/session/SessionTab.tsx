import { useEffect, useRef } from 'react';
import { describeState } from '../selectors/status';
import { useAppStore } from '../store/store';
import { useCommand } from '../store/useCommand';
import { MessageInput } from './MessageInput';
import { Transcript } from './Transcript';
import styles from './SessionTab.module.css';

/** The rich transcript plus an input. VS-Code-extension-like, not a terminal. */
export function SessionTab({ agentId }: { agentId: string }) {
  const { send, error } = useCommand();
  const agent = useAppStore((s) => s.world.agents[agentId]);
  const task = useAppStore((s) => (agent ? s.world.tasks[agent.taskId] : undefined));
  const transcript = useAppStore((s) => s.transcripts[agentId]);
  const bottom = useRef<HTMLDivElement>(null);
  const count = transcript?.items.length ?? 0;

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
          handlers={{
            onAnswer: (requestId, answers) =>
              void send({ type: 'agent.answer', agentId, requestId, answers }),
            onDecide: (requestId, decision, reason) =>
              void send(
                decision === 'approve'
                  ? { type: 'agent.approve', agentId, requestId }
                  : {
                      type: 'agent.deny',
                      agentId,
                      requestId,
                      reason,
                    },
              ),
          }}
        />
        <div ref={bottom} />
      </div>
      {error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}
      <MessageInput
        disabled={agent.state === 'exited'}
        onSend={(text) => send({ type: 'agent.say', agentId, text })}
      />
    </div>
  );
}
