import { useState } from 'react';
import { Markdown } from '../markdown/Markdown';
import type { Agent } from '../protocol/entities';
import type { PlanReviewItem, TranscriptItem } from '../protocol/transcript';
import { documentTab } from '../selectors/tabs';
import { contextBefore } from '../selectors/transcript';
import { PermissionPrompt } from '../session/cards/PermissionPrompt';
import { QuestionPrompt } from '../session/cards/QuestionPrompt';
import { toolCallTitle } from '../session/toolCards';
import { PanelLink } from '../shell/PanelLink';
import { useAppStore } from '../store/store';
import { useCommand } from '../store/useCommand';
import { AppButton } from '../ui/AppButton';
import cards from '../session/cards/cards.module.css';
import styles from './DecisionCard.module.css';

/** The decision block. Both the expanded node and the document tab render this. */
export function DecisionCard({ agent }: { agent: Agent }) {
  const transcript = useAppStore((s) => s.transcripts[agent.id]);
  const { send } = useCommand();
  const requestId = agent.pendingRequestId;
  const items = transcript?.items ?? [];
  // The last match, as the normaliser reads it: a request id names one wait.
  const wait = requestId
    ? [...items].reverse().find((i): i is Waiting => 'requestId' in i && i.requestId === requestId)
    : undefined;
  if (!requestId || !wait) return null;
  const before = contextBefore(items, requestId);

  return (
    <section className={styles.decision} data-testid="decision" data-kind={wait.type}>
      {before.length > 0 && (
        <div className={styles.context}>
          <div className={styles.contextHead}>Before this</div>
          {before.map((item) =>
            item.type === 'message' ? (
              <Markdown key={item.id} source={item.markdown} className={styles.said} />
            ) : (
              <div key={item.id} className={styles.did}>
                <span className={styles.tool}>{item.tool}</span> {toolCallTitle(item)}
              </div>
            ),
          )}
        </div>
      )}
      {wait.type === 'question' && (
        <QuestionPrompt
          item={wait}
          onAnswer={(answers) =>
            send({ type: 'agent.answer', agentId: agent.id, requestId, answers })
          }
        />
      )}
      {wait.type === 'permission_request' && (
        <PermissionPrompt
          item={wait}
          onDecide={(decision, reason) =>
            send(
              decision === 'approve'
                ? { type: 'agent.approve', agentId: agent.id, requestId }
                : { type: 'agent.deny', agentId: agent.id, requestId, reason },
            )
          }
        />
      )}
      {wait.type === 'plan_review' && (
        <PlanReview
          item={wait}
          onDecide={(decision, reason) =>
            send(
              decision === 'approve'
                ? { type: 'agent.approve', agentId: agent.id, requestId }
                : { type: 'agent.deny', agentId: agent.id, requestId, reason },
            )
          }
        />
      )}
    </section>
  );
}

type Waiting = Extract<TranscriptItem, { requestId: string }>;

function PlanReview({
  item,
  onDecide,
}: {
  item: PlanReviewItem;
  onDecide: (decision: 'approve' | 'deny', reason: string) => void | Promise<unknown>;
}) {
  const [reason, setReason] = useState('');
  return (
    <div className={cards.prompt}>
      <div className={cards.qhead}>Plan review</div>
      <div>
        The plan is ready.{' '}
        {item.documentId && <PanelLink tab={documentTab(item.documentId)}>Read the plan</PanelLink>}
      </div>
      <div className={cards.options}>
        <AppButton variant="primary" onClick={() => onDecide('approve', '')}>
          Approve
        </AppButton>
        <input
          className={cards.reasonInput}
          aria-label="Deny reason"
          placeholder="Reason to deny"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        <AppButton disabled={!reason.trim()} onClick={() => onDecide('deny', reason.trim())}>
          Deny
        </AppButton>
      </div>
    </div>
  );
}
