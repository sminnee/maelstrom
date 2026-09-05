import { Markdown } from '../markdown/Markdown';
import type { TranscriptItem } from '../protocol/transcript';
import { clockTime } from '../protocol/time';
import { documentTab } from '../selectors/tabs';
import { PanelLink } from '../shell/PanelLink';
import { AgentMessage } from './cards/AgentMessage';
import { PermissionPrompt } from './cards/PermissionPrompt';
import { QuestionPrompt } from './cards/QuestionPrompt';
import { ResultLine } from './cards/ResultLine';
import { ToolCallCard } from './cards/ToolCallCard';
import { classifyToolCall } from './toolCards';
import { useNow } from '../ui/useNow';
import styles from './Transcript.module.css';

export interface TranscriptHandlers {
  onAnswer?: (requestId: string, answers: Record<string, string>) => void | Promise<unknown>;
  onDecide?: (
    requestId: string,
    decision: 'approve' | 'deny',
    reason: string,
  ) => void | Promise<unknown>;
}

/**
 * Whether an item draws nothing at all.
 *
 * The wait item that follows renders this prompt in full. An empty wrapper
 * would still take a gap slot, so the item takes no row — and no gutter mark.
 * One predicate, because the render loop and {@link gutterMarks} must agree:
 * a skipped item that still claimed a mark would print a time on no row.
 */
function drawsNothing(item: TranscriptItem): boolean {
  return item.type === 'tool_call' && classifyToolCall(item) === 'wait';
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
  /** The wait the expanded card answers, echoed here without controls. */
  deferredRequestId?: string | null;
}) {
  const now = useNow();
  const marks = gutterMarks(items, now);
  return (
    <div className={styles.transcript}>
      {truncatedBefore && <div className={styles.note}>Earlier events were not kept.</div>}
      {items.map((item) => {
        if (drawsNothing(item)) return null;
        const deferred =
          deferredRequestId !== null && 'requestId' in item && item.requestId === deferredRequestId;
        const mark = marks.get(item.id) ?? '';
        return (
          <div
            key={item.id}
            className={styles.card}
            data-testid="transcript-card"
            data-item-type={item.type}
          >
            <span className={styles.gutter}>
              {mark && (
                <time className={styles.time} data-testid="item-time" dateTime={item.ts}>
                  {mark}
                </time>
              )}
            </span>
            {deferred ? <DeferredWait item={item} /> : <Card item={item} handlers={handlers} />}
          </div>
        );
      })}
    </div>
  );
}

/**
 * The time to print beside each item, by item id.
 *
 * A mark only where time moved is what makes the column a timeline: printed on
 * every item it is wallpaper, and the eye stops reading it.
 */
function gutterMarks(items: TranscriptItem[], now: number): Map<string, string> {
  const marks = new Map<string, string>();
  let printed = '';
  for (const item of items) {
    if (drawsNothing(item)) continue;
    const at = clockTime(item.ts, now);
    if (!at || at === printed) continue;
    marks.set(item.id, at);
    printed = at;
  }
  return marks;
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
    case 'gap':
      return (
        <div className={styles.note} data-testid="gap">
          {item.droppedEvents} earlier events were dropped here.
        </div>
      );
    case 'skill':
      return (
        <details className={styles.skill} data-testid="skill">
          <summary className={styles.skillHead}>
            <span className={styles.skillLabel}>skill</span>
            <span className={styles.skillName}>{item.skill}</span>
          </summary>
          <Markdown source={item.markdown} />
        </details>
      );
  }
}
