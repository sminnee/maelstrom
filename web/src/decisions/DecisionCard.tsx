import { useEffect, useId, useRef, useState } from 'react';
import { useAgent, useAnswer, useApprove, useDeny } from '../api/agents';
import type { PendingRequest } from '../api/agents';
import { useAgentStream } from '../live/useAgentStream';
import { Markdown } from '../markdown/Markdown';
import type { Agent } from '../protocol/entities';
import type { PlanReviewItem, TranscriptItem } from '../protocol/transcript';
import { documentTab } from '../selectors/tabs';
import { contextBefore, type ContextItem } from '../selectors/transcript';
import { DecideRow } from '../session/cards/DecideRow';
import { PermissionPrompt } from '../session/cards/PermissionPrompt';
import { QuestionPrompt } from '../session/cards/QuestionPrompt';
import { toolCallTitle } from '../session/toolCards';
import { PanelLink } from '../shell/PanelLink';
import { AppButton } from '../ui/AppButton';
import { useClamped } from '../ui/useClamped';
import cards from '../session/cards/cards.module.css';
import styles from './DecisionCard.module.css';

/**
 * Every decision an agent is waiting on, oldest first. Both the expanded node
 * and the document tab render this. The waits come from the agent's detail, so
 * they render with no transcript open; the context before each comes from the
 * transcript.
 *
 * An agent can be blocked on several at once — see `docs/dev/agent-daemon.md`,
 * "A subagent's permission ask" — and each is answered on its own.
 */
export function DecisionCard({
  agent,
  variant = 'block',
  inDocumentId,
}: {
  agent: Agent;
  variant?: Variant;
  /** The document this is drawn in, so a link back to it can be dropped. */
  inDocumentId?: string;
}) {
  const transcript = useAgentStream(agent.id);
  const detail = useAgent(agent.id);
  const held = new Set(agent.pendingRequestIds);
  const waits = (detail.data?.pendingRequests ?? []).filter((w) => held.has(w.requestId));
  if (waits.length === 0) return null;
  return (
    <>
      {waits.map((wait) => (
        <OneDecision
          key={wait.requestId}
          agent={agent}
          wait={wait}
          items={transcript.items}
          variant={variant}
          inDocumentId={inDocumentId}
        />
      ))}
    </>
  );
}

function OneDecision({
  agent,
  wait,
  items,
  variant,
  inDocumentId,
}: {
  agent: Agent;
  wait: PendingRequest;
  items: TranscriptItem[];
  variant: Variant;
  inDocumentId?: string;
}) {
  const approve = useApprove();
  const deny = useDeny();
  const answer = useAnswer();
  const requestId = wait.requestId;
  const before = contextBefore(items, requestId);
  const decide = (decision: 'approve' | 'deny', reason: string) =>
    decision === 'approve'
      ? approve.mutateAsync({ agentId: agent.id, requestId })
      : deny.mutateAsync({ agentId: agent.id, requestId, reason });

  return (
    <section
      className={styles.decision}
      data-testid="decision"
      data-kind={wait.type}
      data-variant={variant}
      // A ribbon suits a decision that is one act: approve, or deny with a
      // reason. A question is a multi-step form, so it keeps its card even in
      // the dock — laying its steps out across a band would break it.
      data-binary={wait.type === 'question' ? undefined : ''}
    >
      {before.length > 0 && <ContextRail items={before} variant={variant} />}
      {wait.type === 'question' && (
        <QuestionPrompt
          item={wait}
          onAnswer={(answers) => answer.mutateAsync({ agentId: agent.id, requestId, answers })}
        />
      )}
      {wait.type === 'permission_request' && <PermissionPrompt item={wait} onDecide={decide} />}
      {wait.type === 'plan_review' && (
        <PlanReview item={wait} onDecide={decide} inDocumentId={inDocumentId} />
      )}
    </section>
  );
}

/**
 * Which surface the decision is drawn on. `block` is the expanded node card,
 * where the context explains the ask on sight. `dock` is the band under a
 * document, where the context is a control. See `web/DESIGN.md`, "Review Dock".
 */
type Variant = 'block' | 'dock';

/**
 * The context before a wait: the last things the agent said or did.
 *
 * `contextBefore` caps this at three items, but an item may be a whole message,
 * so three items can still fill the pane. Each surface bounds that differently
 * — see `web/DESIGN.md`, "Decision" and "Review Dock".
 *
 * Neither state is persisted, for the reason the panel's tabs and filters are
 * not: view state is not held. Unsent input is — see `docs/dev/orchestrator-ui.md`,
 * "Holding what was typed".
 */
function ContextRail({ items, variant }: { items: ContextItem[]; variant: Variant }) {
  return variant === 'dock' ? <DockedContext items={items} /> : <InlineContext items={items} />;
}

/** The items themselves, in the one shape both surfaces draw them in. */
function ContextItems({ items }: { items: ContextItem[] }) {
  return items.map((item) =>
    item.type === 'message' ? (
      <Markdown key={item.id} source={item.markdown} className={styles.said} />
    ) : (
      <div key={item.id} className={styles.did}>
        <span className={styles.tool}>{item.tool}</span> {toolCallTitle(item)}
      </div>
    ),
  );
}

/**
 * The dock's context: a control, and a sheet it opens over the document.
 *
 * The sheet overlaps content it is not part of, so it earns a shadow under the
 * Overlap Test. Escape closes it, because anything that covers the page must
 * give the page back from the keyboard.
 */
function DockedContext({ items }: { items: ContextItem[] }) {
  const [open, setOpen] = useState(false);
  const sheetId = useId();

  // On the document, not the button: the sheet scrolls and holds links, so the
  // reader can be focused inside it when they reach for Escape. Stop the event
  // there, or the panel and the card close on the same key.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.stopPropagation();
      setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  return (
    <div className={styles.docked}>
      <AppButton
        variant="quiet"
        className={styles.contextToggle}
        aria-expanded={open}
        aria-controls={sheetId}
        onClick={() => setOpen((was) => !was)}
      >
        Before this · {items.length}
      </AppButton>
      {open && (
        <div className={styles.sheet} id={sheetId} data-testid="decision-context">
          <ContextItems items={items} />
        </div>
      )}
    </div>
  );
}

/**
 * The card's context: inline, open, and clamped.
 *
 * The clamp bounds the height of context the operator wants; the fold removes
 * context they have already read. Two controls, because they answer different
 * questions.
 */
function InlineContext({ items }: { items: ContextItem[] }) {
  const [expanded, setExpanded] = useState(false);
  const body = useRef<HTMLDivElement>(null);
  const bodyId = useId();
  const clamped = useClamped(body, [items, expanded]);

  return (
    <details
      className={styles.context}
      open
      data-testid="decision-context"
      // Folding it away resets the clamp, so re-opening gives the short rail
      // rather than whatever height it was left at.
      onToggle={(e) => !e.currentTarget.open && setExpanded(false)}
    >
      <summary className={styles.contextHead}>Before this</summary>
      <div
        className={styles.contextBody}
        ref={body}
        id={bodyId}
        data-expanded={expanded || undefined}
      >
        <ContextItems items={items} />
      </div>
      {(clamped || expanded) && (
        <AppButton
          className={styles.more}
          variant="quiet"
          aria-expanded={expanded}
          aria-controls={bodyId}
          onClick={() => setExpanded((was) => !was)}
        >
          {expanded ? 'Show less' : 'Show more'}
        </AppButton>
      )}
    </details>
  );
}

function PlanReview({
  item,
  onDecide,
  inDocumentId,
}: {
  item: PlanReviewItem;
  onDecide: (decision: 'approve' | 'deny', reason: string) => void | Promise<unknown>;
  inDocumentId?: string;
}) {
  // In the plan's own tab the link leads nowhere, and on a phone it pushed a
  // second copy of the screen the reader is already on.
  const link = item.documentId && item.documentId !== inDocumentId ? item.documentId : null;
  return (
    <div className={cards.prompt}>
      <div className={cards.qhead} data-role="prompt-head">
        Plan review
      </div>
      <div data-role="prompt-text" data-linked={link ? '' : undefined}>
        {link ? (
          <>
            The plan is ready. <PanelLink tab={documentTab(link)}>Read the plan</PanelLink>
          </>
        ) : (
          'The plan is ready.'
        )}
      </div>
      <DecideRow onDecide={onDecide} />
    </div>
  );
}
