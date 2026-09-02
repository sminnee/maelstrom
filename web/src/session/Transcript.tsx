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
}: {
  items: TranscriptItem[];
  truncatedBefore: boolean;
  handlers?: TranscriptHandlers;
}) {
  return (
    <div className={styles.transcript}>
      {truncatedBefore && <div className={styles.note}>Earlier events were not kept.</div>}
      {items.map((item) => (
        <div
          key={item.id}
          className={styles.card}
          data-testid="transcript-card"
          data-item-type={item.type}
        >
          <Card item={item} handlers={handlers} />
        </div>
      ))}
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
        <div className={styles.plan} data-decision={item.decision}>
          <span>Plan review</span>
          {documentId && <PanelLink tab={documentTab(documentId)}>Plan</PanelLink>}
          <span className={styles.decision}>{item.decision ?? 'awaiting review'}</span>
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
