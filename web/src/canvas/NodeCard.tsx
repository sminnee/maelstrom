import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ViewportPortal, useReactFlow } from '@xyflow/react';
import { useStop } from '../api/agents';
import { useRemoveFromDesk } from '../api/desk';
import { useLaunch, useSetStatus, useTask } from '../api/tasks';
import { useWorld } from '../api/useWorld';
import { useAgentStream } from '../live/useAgentStream';
import { DecisionCard } from '../decisions/DecisionCard';
import { Markdown } from '../markdown/Markdown';
import { deskIdForAgent, deskIdForTask } from '../protocol/deskId';
import { driftFixLabel, driftSentence } from '../protocol/progress';
import type { GraphNode } from '../selectors/graph';
import { isLive, nodeTitle } from '../selectors/graph';
import { describeDocumentStatus } from '../selectors/status';
import { documentTab, sessionTab } from '../selectors/tabs';
import { toolCallTitle } from '../session/toolCards';
import { PanelLink } from '../shell/PanelLink';
import { useAppStore } from '../store/store';
import { phaseLabel } from '../protocol/phase';
import { AppButton } from '../ui/AppButton';
import { StatusPicker } from '../ui/StatusPicker';
import { NODE } from './layout';
import styles from './NodeCard.module.css';

/** The card's width in flow units. Its height comes from its content. */
export const CARD_WIDTH = 440;

const EASE = 'cubic-bezier(0.16, 1, 0.3, 1)';
const GROW_MS = 260;
const SHRINK_MS = 180;
const FADE_MS = 120;

/**
 * The expanded node's card, in React Flow's viewport portal so it pans and
 * zooms with the canvas. `open` false plays the collapse, then calls `onClosed`.
 */
export function NodeCard({
  node,
  position,
  open,
  onClosed,
}: {
  node: GraphNode;
  position: { x: number; y: number };
  open: boolean;
  onClosed: () => void;
}) {
  const { world } = useWorld();
  const transcript = useAgentStream(node.agent?.id ?? null);
  const collapseNode = useAppStore((s) => s.collapseNode);
  const launch = useLaunch();
  const stop = useStop();
  const setStatus = useSetStatus();
  const removeFromDesk = useRemoveFromDesk();
  const { getViewport, setViewport } = useReactFlow();
  const card = useRef<HTMLDivElement>(null);
  const inner = useRef<HTMLDivElement>(null);
  const briefBox = useRef<HTMLDivElement>(null);
  const [expandedContent, setExpandedContent] = useState(false);
  const [longContent, setLongContent] = useState(false);
  const [picking, setPicking] = useState(false);
  const { task, agent, worktree } = node;
  // The list holds slim rows, so the brief comes from the task's detail.
  const detail = useTask(task?.id ?? null);
  const brief = detail.data?.content.trim() ?? '';

  // A card that runs past the canvas edge pans into view. It measures the
  // laid-out box (the grow animation only plays towards it), and again
  // whenever the content changes the card's size.
  useLayoutEffect(() => {
    const el = card.current;
    const pane = el?.closest('.react-flow');
    if (!el || !pane || typeof el.getBoundingClientRect !== 'function') return;
    const intoView = () => {
      const box = el.getBoundingClientRect();
      const edge = pane.getBoundingClientRect();
      if (box.width === 0 || box.height === 0) return;
      const margin = 16;
      const dx = Math.min(0, edge.right - margin - box.right);
      const dy = Math.min(0, edge.bottom - margin - box.bottom);
      if (dx === 0 && dy === 0) return;
      const { x, y, zoom } = getViewport();
      void setViewport({ x: x + dx, y: y + dy, zoom }, { duration: 300 });
    };
    intoView();
    if (typeof ResizeObserver !== 'function') return;
    const observer = new ResizeObserver(intoView);
    observer.observe(el);
    return () => observer.disconnect();
  }, [getViewport, setViewport]);

  // The clamp is a rendered height, so only the rendered brief says whether it
  // overflows. Counting source lines misses a long line that wraps, and offers
  // a toggle on short lines that already fit.
  useLayoutEffect(() => {
    const el = briefBox.current;
    if (!el) return;
    const measure = () => setLongContent(el.scrollHeight > el.clientHeight + 1);
    measure();
    if (typeof ResizeObserver !== 'function') return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [brief, expandedContent]);

  // Grow from the node's size to the card's measured size; the content fades in after.
  useLayoutEffect(() => {
    const el = card.current;
    const body = inner.current;
    el?.focus({ preventScroll: true });
    if (!el || !body || typeof el.animate !== 'function') return;
    if (reducedMotion()) {
      el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: FADE_MS });
      return;
    }
    el.animate(
      [
        { width: `${NODE.width}px`, height: `${NODE.height}px` },
        { width: `${el.offsetWidth}px`, height: `${el.offsetHeight}px` },
      ],
      { duration: GROW_MS, easing: EASE },
    );
    body.animate([{ opacity: 0 }, { opacity: 1 }], {
      duration: GROW_MS - FADE_MS,
      delay: FADE_MS,
      fill: 'backwards',
    });
  }, []);

  // Collapse reverses the grow, then the card unmounts.
  useEffect(() => {
    if (open) return;
    const el = card.current;
    if (!el || typeof el.animate !== 'function') {
      onClosed();
      return;
    }
    const to = reducedMotion()
      ? { opacity: 0 }
      : { width: `${NODE.width}px`, height: `${NODE.height}px`, opacity: 0 };
    const animation = el.animate([{ opacity: 1 }, to], {
      duration: SHRINK_MS,
      easing: 'ease-in',
      fill: 'forwards',
    });
    let live = true;
    const done = () => {
      if (live) onClosed();
    };
    animation.finished.then(done, done);
    // Reopened mid-collapse: keep the card rather than unmounting it.
    return () => {
      live = false;
      animation.cancel();
    };
  }, [open, onClosed]);

  // Esc collapses the card, unless the panel is handling it (a composer, an input).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if ((e.target as Element | null)?.closest?.('[data-testid="panel"]')) return;
      collapseNode();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [collapseNode]);

  // A free agent has no task, so it owns no documents.
  const documents = task ? Object.values(world.documents).filter((d) => d.taskId === task.id) : [];
  // The worktree is where the agent runs, so its branch beats the frontmatter.
  const where = worktree ?? (agent ? world.worktrees[agent.worktreeId] : undefined);
  const meta = [
    where?.branch || task?.branch || '',
    where?.nato || (agent ? agent.worktreeId : ''),
    agent?.model || task?.model || '',
    agent?.permissionMode || '',
    agent?.costUsd ? `$${agent.costUsd.toFixed(2)}` : '',
  ].filter(Boolean);
  const title = nodeTitle(node);
  const deciding = !!agent && agent.state.startsWith('awaiting-') && !!agent.pendingRequestId;
  const running = [...transcript.items]
    .reverse()
    .find((i) => i.type === 'tool_call' && i.status === 'running');
  const now = agent?.lastMessage ?? '';

  return (
    <ViewportPortal>
      <div
        ref={card}
        className={`${styles.card} nowheel nopan nodrag`}
        role="dialog"
        aria-label={title}
        tabIndex={-1}
        data-phase={node.phase ?? undefined}
        data-state={node.progress.state}
        style={{ transform: `translate(${position.x}px, ${position.y}px)` }}
      >
        <div ref={inner} className={styles.inner}>
          <header className={styles.header}>
            <div className={styles.titleBlock}>
              <h2 className={styles.title}>{title}</h2>
              <div className={styles.idLine}>
                {node.showProject && task && <span className={styles.project}>{task.project}</span>}
                <span className={styles.id}>{task ? task.notebookId : node.id.slice(0, 8)}</span>
                {node.phase && <span className={styles.phase}>{phaseLabel(node.phase)}</span>}
              </div>
              {meta.length > 0 && <div className={styles.meta}>{meta.join(' · ')}</div>}
            </div>
            <button
              type="button"
              className={styles.close}
              aria-label="Collapse"
              onClick={collapseNode}
            >
              ×
            </button>
          </header>

          <div className={styles.status} data-state={node.progress.state}>
            {/*
             * The status control at the right says it already when the words
             * would only echo it. The dot goes with them: it reads the state,
             * so alone it says nothing.
             */}
            {!node.progress.echoesStatus && (
              <>
                <span className={styles.dot} aria-hidden="true" />
                <span className={styles.stateText}>{node.progress.words}</span>
              </>
            )}
            {!deciding && node.reason && <span className={styles.reason}>{node.reason}</span>}
            {task && (
              <StatusPicker
                task={task}
                className={styles.taskStatus}
                label={`Status of ${task.title}`}
                picking={picking}
                onPick={() => setPicking(true)}
                onDone={() => setPicking(false)}
                onChange={(status) => {
                  setPicking(false);
                  return setStatus.mutateAsync({ taskId: task.id, status });
                }}
              />
            )}
          </div>

          {task && node.progress.drift && (
            <div className={styles.drift} data-testid="drift-band">
              <span className={styles.driftMark} aria-hidden="true">
                ▲
              </span>
              <span className={styles.driftText}>{driftSentence(node.progress, task.status)}</span>
              {node.progress.fixStatus && (
                <AppButton
                  variant="quiet"
                  onClick={() =>
                    setStatus.mutateAsync({ taskId: task.id, status: node.progress.fixStatus! })
                  }
                >
                  {driftFixLabel(node.progress.fixStatus)}
                </AppButton>
              )}
            </div>
          )}

          {brief && (
            <div
              className={styles.content}
              data-testid="task-content"
              data-expanded={expandedContent}
            >
              <div ref={briefBox} className={styles.briefBox} id={`brief-${node.id}`}>
                <Markdown source={brief} className={styles.brief} />
              </div>
              {(longContent || expandedContent) && (
                <button
                  type="button"
                  className={styles.more}
                  aria-expanded={expandedContent}
                  aria-controls={`brief-${node.id}`}
                  onClick={() => setExpandedContent((open) => !open)}
                >
                  {expandedContent ? 'Less' : 'More'}
                </button>
              )}
            </div>
          )}

          {deciding && agent ? (
            <DecisionCard agent={agent} />
          ) : (
            (now || running) && (
              <div className={styles.now}>
                <span className={styles.nowHead}>Now</span>
                <span className={styles.nowText}>
                  {now}
                  {running && running.type === 'tool_call' && (
                    <span className={styles.running}>
                      {running.tool} {toolCallTitle(running)}
                    </span>
                  )}
                </span>
              </div>
            )
          )}

          <footer className={styles.footer}>
            <div className={styles.links}>
              {agent && <PanelLink tab={sessionTab(agent.id)}>Session</PanelLink>}
              {documents.map((d) => (
                <PanelLink key={d.id} tab={documentTab(d.id)}>
                  {d.title} v{d.version} · {describeDocumentStatus(d.status)}
                </PanelLink>
              ))}
            </div>
            <div className={styles.commands}>
              {!agent && task?.actionable && (
                <AppButton
                  variant="primary"
                  processingChildren="Launching"
                  onClick={() => launch.mutateAsync({ taskId: task.id })}
                >
                  Launch
                </AppButton>
              )}
              {node.kind === 'freeAgent' && agent && (
                <AppButton
                  variant="quiet"
                  // Disabled while live: the node draws regardless, so a
                  // dismiss now would do nothing.
                  disabled={isLive(agent)}
                  onClick={async () => {
                    await removeFromDesk.mutateAsync({ id: deskIdForAgent(agent.id) });
                    collapseNode();
                  }}
                >
                  Dismiss
                </AppButton>
              )}
              {/* Hidden rather than disabled, unlike Dismiss above: a task keeps
                  its task list row as the other way off the desk. */}
              {node.kind === 'task' && task && !isLive(agent) && (
                <AppButton
                  variant="quiet"
                  onClick={async () => {
                    await removeFromDesk.mutateAsync({ id: deskIdForTask(task.id) });
                    collapseNode();
                  }}
                >
                  Remove from desk
                </AppButton>
              )}
              {agent && isLive(agent) && (
                <AppButton variant="quiet" onClick={() => stop.mutateAsync({ agentId: agent.id })}>
                  Stop
                </AppButton>
              )}
            </div>
          </footer>
        </div>
      </div>
    </ViewportPortal>
  );
}

function reducedMotion(): boolean {
  return (
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}
