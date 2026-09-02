import { useState } from 'react';
import type { Attention } from '../protocol/attention';
import type { Agent, Task } from '../protocol/entities';
import type { QuestionItem } from '../protocol/transcript';
import { useBackend } from '../store/backendContext';
import { useAppStore } from '../store/store';
import { documentTab, sessionTab } from '../selectors/tabs';
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
  const backend = useBackend();
  const openTab = useAppStore((s) => s.openTab);
  const transcripts = useAppStore((s) => s.transcripts);
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);

  const send = async (cmd: Parameters<typeof backend.command>[0]) => {
    setError(null);
    const reply = await backend.command(cmd);
    if (!reply.ok) setError(`${reply.error.code}: ${reply.error.message}`);
    else if (cmd.type === 'agent.deny') setReason('');
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
        <button type="button" onClick={() => send({ type: 'agent.launch', taskId: task.id })}>
          Launch
        </button>
      )}
      {agent &&
        requestId &&
        (agent.state === 'awaiting-plan-review' || agent.state === 'awaiting-permission') && (
          <div className={styles.row}>
            <button
              type="button"
              onClick={() => send({ type: 'agent.approve', agentId: agent.id, requestId })}
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
              onClick={() => send({ type: 'agent.deny', agentId: agent.id, requestId, reason })}
            >
              Deny
            </button>
            {planDoc && (
              <button type="button" onClick={() => openTab(documentTab(planDoc))}>
                Open plan
              </button>
            )}
          </div>
        )}
      {agent && requestId && question && (
        <div className={styles.questions}>
          {question.questions.map((q) => (
            <div key={q.question} className={styles.question}>
              <div className={styles.qtext}>{q.question}</div>
              <div className={styles.row}>
                {q.options.map((o) => (
                  <button
                    key={o.label}
                    type="button"
                    title={o.description}
                    onClick={() =>
                      send({
                        type: 'agent.answer',
                        agentId: agent.id,
                        requestId,
                        answers: { [q.question]: o.label },
                      })
                    }
                  >
                    {o.label}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
      {agent && (
        <div className={styles.row}>
          <button type="button" onClick={() => openTab(sessionTab(agent.id))}>
            Open session
          </button>
          {agent.state !== 'exited' && (
            <button type="button" onClick={() => send({ type: 'agent.stop', agentId: agent.id })}>
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
