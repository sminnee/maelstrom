import { useState } from 'react';
import type { Attention } from '../protocol/attention';
import type { Agent, Task } from '../protocol/entities';
import type { QuestionItem } from '../protocol/transcript';
import { documentTab, sessionTab } from '../selectors/tabs';
import { QuestionPrompt } from '../session/cards/QuestionPrompt';
import { PanelLink } from '../shell/PanelLink';
import { useAppStore } from '../store/store';
import { useCommand } from '../store/useCommand';
import styles from './QuickActions.module.css';

/** Approve, deny, answer, launch, open session: the commands a summary can send. */
export function QuickActions({
  task,
  agent,
  attention,
}: {
  task: Task;
  agent: Agent | undefined;
  attention: Attention[];
}) {
  const { send, error } = useCommand();
  const transcripts = useAppStore((s) => s.transcripts);
  const [reason, setReason] = useState('');

  const deny = async (agentId: string, requestId: string) => {
    if (await send({ type: 'agent.deny', agentId, requestId, reason })) setReason('');
  };

  const requestId = agent?.pendingRequestId ?? null;
  const planDoc = attention.find((a) => a.kind === 'plan_review')?.documentId ?? null;
  const question =
    agent && agent.state === 'awaiting-question'
      ? ((transcripts[agent.id]?.items ?? []).find(
          (i): i is QuestionItem => i.type === 'question' && i.requestId === requestId,
        ) ?? null)
      : null;

  return (
    <section className={styles.actions} data-testid="quick-actions">
      {!agent && task.actionable && (
        <button type="button" onClick={() => void send({ type: 'agent.launch', taskId: task.id })}>
          Launch
        </button>
      )}
      {agent &&
        requestId &&
        (agent.state === 'awaiting-plan-review' || agent.state === 'awaiting-permission') && (
          <div className={styles.row}>
            <button
              type="button"
              onClick={() => void send({ type: 'agent.approve', agentId: agent.id, requestId })}
            >
              Approve
            </button>
            <input
              className={styles.reason}
              placeholder="Reason to deny"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              aria-label="Deny reason"
            />
            <button
              type="button"
              disabled={!reason.trim()}
              onClick={() => void deny(agent.id, requestId)}
            >
              Deny
            </button>
            {planDoc && <PanelLink tab={documentTab(planDoc)}>Plan</PanelLink>}
          </div>
        )}
      {agent && requestId && question && (
        <QuestionPrompt
          item={question}
          onAnswer={(answers) =>
            void send({ type: 'agent.answer', agentId: agent.id, requestId, answers })
          }
        />
      )}
      {agent && (
        <div className={styles.row}>
          <PanelLink tab={sessionTab(agent.id)}>Session</PanelLink>
          {agent.state !== 'exited' && (
            <button
              type="button"
              onClick={() => void send({ type: 'agent.stop', agentId: agent.id })}
            >
              Stop
            </button>
          )}
        </div>
      )}
      {error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}
    </section>
  );
}
