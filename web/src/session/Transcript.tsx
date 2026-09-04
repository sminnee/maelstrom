import type { TranscriptItem } from '../protocol/transcript';
import { documentTab } from '../selectors/tabs';
import { PanelLink } from '../shell/PanelLink';
import { AgentMessage } from './cards/AgentMessage';
import { PermissionPrompt } from './cards/PermissionPrompt';
import { QuestionPrompt } from './cards/QuestionPrompt';
import { ResultLine } from './cards/ResultLine';
import { ToolCallCard } from './cards/ToolCallCard';
import styles from './Transcript.module.css';

export interface TranscriptHandlers {
  onAnswer?: (requestId: string, answers: Record<string, string>) => void;
  onDecide?: (requestId: string, decision: 'approve' | 'deny', reason: string) => void;
}

/** The rich transcript: one card per item, in order. */
export function Transcript({
  items,
  truncatedBefore,
  handlers = {},
  deferredRequestId = null,
}: {
  items: TranscriptItem[];
  truncatedBefore: boolean;
  handlers?: TranscriptHandlers;
  /** The wait the expanded card answers. Shown here as an echo, without controls. */
  deferredRequestId?: string | null;
}) {
  return (
    <div className={styles.transcript}>
      {truncatedBefore && <div className={styles.note}>Earlier events were not kept.</div>}
      {items.map((item) => {
        const deferred =
          deferredRequestId !== null && 'requestId' in item && item.requestId === deferredRequestId;
        return (
          <div
            key={item.id}
            className={styles.card}
            data-testid="transcript-card"
            data-item-type={item.type}
          >
            {deferred ? <DeferredWait item={item} /> : <Card item={item} handlers={handlers} />}
          </div>
        );
      })}
    </div>
  );
}

/**
 * A wait the expanded card is answering. The panel reads, so this says what the
 * agent asked and where the answer goes, and carries no controls of its own.
 */
function DeferredWait({ item }: { item: TranscriptItem }) {
  const asked =
    item.type === 'question'
      ? (item.questions[0]?.question ?? 'A question')
      : item.type === 'permission_request'
        ? `Permission: ${item.tool}`
        : 'A plan is ready for review';
  return (
    <div className={styles.deferred} data-testid="deferred-wait">
      <span className={styles.deferredAsk}>{asked}</span>
      <span className={styles.deferredWhere}>Answering on the canvas</span>
    </div>
  );
}

function Card({ item, handlers }: { item: TranscriptItem; handlers: TranscriptHandlers }) {
  switch (item.type) {
    case 'message':
      return <AgentMessage item={item} />;
    case 'tool_call':
      return <ToolCallCard item={item} />;
    case 'question':
      return (
        <QuestionPrompt
          item={item}
          onAnswer={handlers.onAnswer ? (a) => handlers.onAnswer?.(item.requestId, a) : undefined}
        />
      );
    case 'permission_request':
      return (
        <PermissionPrompt
          item={item}
          onDecide={
            handlers.onDecide
              ? (d, reason) => handlers.onDecide?.(item.requestId, d, reason)
              : undefined
          }
        />
      );
    case 'plan_review': {
      const documentId = item.documentId;
      return (
        <div
          className={styles.plan}
          data-decision={item.decision}
          data-stale={item.stale || undefined}
        >
          <span>Plan review</span>
          {documentId && <PanelLink tab={documentTab(documentId)}>Plan</PanelLink>}
          <span className={styles.decision}>
            {item.decision ?? (item.stale ? 'no longer pending' : 'awaiting review')}
          </span>
          {item.reason && <div className={styles.reason}>{item.reason}</div>}
        </div>
      );
    }
    case 'turn_result':
      return <ResultLine item={item} />;
    case 'system':
      return (
        <div className={styles.system}>
          session {item.sessionId.slice(0, 8)} · {item.model}
        </div>
      );
    case 'error':
      return <div className={styles.error}>{item.message}</div>;
  }
}
