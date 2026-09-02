import { useEffect, useLayoutEffect, useRef } from 'react';
import { ViewportPortal } from '@xyflow/react';
import { DecisionCard } from '../decisions/DecisionCard';
import type { GraphNode } from '../selectors/graph';
import { describeState } from '../selectors/status';
import { documentTab, sessionTab } from '../selectors/tabs';
import { toolCallTitle } from '../session/toolCards';
import { PanelLink } from '../shell/PanelLink';
import { useAppStore } from '../store/store';
import { useCommand } from '../store/useCommand';
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
  const world = useAppStore((s) => s.world);
  const transcript = useAppStore((s) => s.transcripts[node.agent?.id ?? '']);
  const collapseNode = useAppStore((s) => s.collapseNode);
  const { send, error } = useCommand();
  const card = useRef<HTMLDivElement>(null);
  const inner = useRef<HTMLDivElement>(null);
  const { task, agent } = node;

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

  const documents = Object.values(world.documents).filter((d) => d.taskId === task.id);
  const worktree = agent ? world.worktrees[agent.worktreeId] : undefined;
  const meta = [
    task.branch,
    worktree?.nato ?? (agent ? agent.worktreeId : ''),
    agent?.model ?? task.model,
    agent?.costUsd ? `$${agent.costUsd.toFixed(2)}` : '',
  ].filter(Boolean);
  const deciding = !!agent && agent.state.startsWith('awaiting-') && !!agent.pendingRequestId;
  const running = [...(transcript?.items ?? [])]
    .reverse()
    .find((i) => i.type === 'tool_call' && i.status === 'running');
  const now = agent?.lastMessage ?? '';

  return (
    <ViewportPortal>
      <div
        ref={card}
        className={`${styles.card} nowheel nopan nodrag`}
        role="dialog"
        aria-label={task.title}
        tabIndex={-1}
        data-phase={task.phase}
        data-state={node.state}
        style={{ transform: `translate(${position.x}px, ${position.y}px)` }}
      >
        <div ref={inner} className={styles.inner}>
          <header className={styles.header}>
            <div className={styles.titleBlock}>
              <h2 className={styles.title}>{task.title}</h2>
              <div className={styles.idLine}>
                <span className={styles.id}>{task.id}</span>
                <span className={styles.phase}>{task.phase}</span>
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

          <div className={styles.status} data-state={node.state}>
            <span className={styles.dot} aria-hidden="true" />
            <span className={styles.stateText}>{describeState(task, agent)}</span>
            {!deciding && node.reason && <span className={styles.reason}>{node.reason}</span>}
          </div>

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
                  {d.title} v{d.version} · {d.status.replace('-', ' ')}
                </PanelLink>
              ))}
            </div>
            <div className={styles.commands}>
              {!agent && task.actionable && (
                <button
                  type="button"
                  className={styles.primary}
                  onClick={() => void send({ type: 'agent.launch', taskId: task.id })}
                >
                  Launch
                </button>
              )}
              {agent && agent.state !== 'exited' && (
                <button
                  type="button"
                  className={styles.quiet}
                  onClick={() => void send({ type: 'agent.stop', agentId: agent.id })}
                >
                  Stop
                </button>
              )}
            </div>
          </footer>
          {error && (
            <div className={styles.error} role="alert">
              {error}
            </div>
          )}
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
