import { Markdown } from '../markdown/Markdown';
import type { TranscriptItem } from '../protocol/transcript';
import { clockTime } from '../protocol/time';
import { contextFigure, contextSize } from '../protocol/tokens';
import { documentTab } from '../selectors/tabs';
import { PanelLink } from '../shell/PanelLink';
import { AgentMessage } from './cards/AgentMessage';
import { BashCard } from './cards/BashCard';
import { PermissionPrompt } from './cards/PermissionPrompt';
import { QuestionPrompt } from './cards/QuestionPrompt';
import { ResultLine } from './cards/ResultLine';
import { ToolCallCard } from './cards/ToolCallCard';
import { classifyToolCall } from './toolCards';
import cards from './cards/cards.module.css';
import { AppButton } from '../ui/AppButton';
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
 * Which of the transcript's two registers an item draws in.
 *
 * The spacing rule and the card chrome both need this, and deriving it twice is
 * how they drift: a shell item drew `BashCard` while the CSS read its item type
 * and spaced it as prose. One value, read by both.
 */
function registerOf(item: TranscriptItem): 'ledger' | 'prose' {
  return item.type === 'tool_call' ? 'ledger' : 'prose';
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
  deferredRequestIds = [],
  hiddenCount = 0,
  revealSize,
  onShowMore,
}: {
  items: TranscriptItem[];
  truncatedBefore: boolean;
  handlers?: TranscriptHandlers;
  /** The wait the expanded card answers, echoed here without controls. */
  /** Asks whose prompt the canvas owns; the transcript echoes them read-only. */
  deferredRequestIds?: readonly string[];
  /**
   * Events above `items` that the session tab holds back, so the button can
   * say how many a click reveals.
   */
  hiddenCount?: number;
  /** How many a click reveals, so the button can promise only what it does. */
  revealSize?: number;
  onShowMore?: () => void;
}) {
  const now = useNow();
  const marks = gutterMarks(items, now);
  return (
    <div className={styles.transcript}>
      {truncatedBefore && <div className={styles.note}>Earlier events were not kept.</div>}
      {hiddenCount > 0 && (
        <AppButton variant="quiet" className={styles.showMore} onClick={onShowMore}>
          Show {Math.min(revealSize ?? hiddenCount, hiddenCount)} earlier events
        </AppButton>
      )}
      {items.map((item) => {
        if (drawsNothing(item)) return null;
        const deferred = 'requestId' in item && deferredRequestIds.includes(item.requestId);
        const mark = marks.get(item.id) ?? '';
        return (
          <div
            key={item.id}
            className={styles.card}
            data-testid="transcript-card"
            data-item-type={item.type}
            data-register={registerOf(item)}
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
 *
 * Computed over the drawn window, not the whole session. The first visible item
 * therefore takes a mark whenever it carries a readable time, where in the full
 * list it might have repeated the one above it. That also keeps the pass
 * O(window) on every 30s tick.
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
    case 'compact':
      // A rule here where the tool row has none — see
      // `docs/dev/orchestrator-ui.md` for why the two differ.
      return (
        <div className={styles.compact} data-testid="compact">
          <span className={styles.compactLabel}>
            compacted
            {/* The unit lands once, on the figure it ends on. A boundary that
                reported no counts says only that it happened. */}
            {item.preTokens > 0 && (
              <>
                {' · '}
                {contextFigure(item.preTokens)} → {contextSize(item.postTokens) || '0 ctx'}
              </>
            )}
          </span>
        </div>
      );
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
    case 'shell':
      // Not a Bash call. `CONTEXT.md` keeps the two apart: this is context the
      // host injected on the operator's behalf, so it reads as something they
      // did rather than sinking into the agent's ledger.
      return (
        <div className={cards.shell} data-testid="shell">
          <div className={cards.shellHead}>you ran</div>
          <BashCard command={item.command} output={item.output} status={item.status} />
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
    case 'compact_summary':
      // The same fold as a skill, and for the same reason: a long body the
      // harness wrote, which would otherwise read as something the operator
      // typed. It shares the chrome rather than restating it.
      return (
        <details className={styles.skill} data-testid="compact-summary">
          <summary className={styles.skillHead}>
            <span className={styles.skillLabel}>carried over</span>
          </summary>
          <Markdown source={item.markdown} />
        </details>
      );
  }
}
