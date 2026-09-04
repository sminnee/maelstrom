import { useState } from 'react';
import { useAgent, useAnswer, useApprove, useDeny } from '../api/agents';
import { useAgentStream } from '../live/useAgentStream';
import { Markdown } from '../markdown/Markdown';
import type { Agent } from '../protocol/entities';
import type { PlanReviewItem } from '../protocol/transcript';
import { documentTab } from '../selectors/tabs';
import { contextBefore } from '../selectors/transcript';
import { PermissionPrompt } from '../session/cards/PermissionPrompt';
import { QuestionPrompt } from '../session/cards/QuestionPrompt';
import { toolCallTitle } from '../session/toolCards';
import { PanelLink } from '../shell/PanelLink';
import { AppButton } from '../ui/AppButton';
import cards from '../session/cards/cards.module.css';
import styles from './DecisionCard.module.css';

/**
 * The decision block. Both the expanded node and the document tab render
 * this. The wait itself comes from the agent's detail, so it renders with no
 * transcript open; the context before it comes from the transcript.
 */
export function DecisionCard({ agent }: { agent: Agent }) {
  const transcript = useAgentStream(agent.id);
  const detail = useAgent(agent.id);
  const approve = useApprove();
  const deny = useDeny();
  const answer = useAnswer();
  const requestId = agent.pendingRequestId;
  const items = transcript.items;
  const wait = detail.data?.pendingRequest;
  if (!requestId || !wait || wait.requestId !== requestId) return null;
  const before = contextBefore(items, requestId);
  const decide = (decision: 'approve' | 'deny', reason: string) =>
    decision === 'approve'
      ? approve.mutateAsync({ agentId: agent.id, requestId })
      : deny.mutateAsync({ agentId: agent.id, requestId, reason });

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
          onAnswer={(answers) => answer.mutateAsync({ agentId: agent.id, requestId, answers })}
        />
      )}
      {wait.type === 'permission_request' && <PermissionPrompt item={wait} onDecide={decide} />}
      {wait.type === 'plan_review' && <PlanReview item={wait} onDecide={decide} />}
    </section>
  );
}

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
