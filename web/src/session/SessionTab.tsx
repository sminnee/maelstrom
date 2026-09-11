import { useEffect, useRef } from 'react';
import { useAnswer, useApprove, useDeny, useRun, useSay, useSetMode } from '../api/agents';
import { useWorld } from '../api/useWorld';
import { useAgentStream } from '../live/useAgentStream';
import type { Agent } from '../protocol/entities';
import { nextMode } from '../protocol/modes';
import { contextSize } from '../protocol/tokens';
import { finishedSubagentsOf, subagentsOf } from '../selectors/agents';
import { progressOf } from '../protocol/progress';
import { sessionTab } from '../selectors/tabs';
import { answeredOnCanvas } from '../selectors/transcript';
import { PanelLink } from '../shell/PanelLink';
import { useAppStore } from '../store/store';
import { AppButton } from '../ui/AppButton';
import { awaitCompact } from './awaitCompact';
import { MessageInput } from './MessageInput';
import { Transcript } from './Transcript';
import styles from './SessionTab.module.css';

/**
 * What `Compact` says to the agent.
 *
 * A slash command reaches Claude Code as the text of a user turn. That is the
 * path `task.build_prompt` already uses, so this is an ordinary `say` rather
 * than a command of maelstrom's own.
 */
const COMPACT_COMMAND = '/compact';

/**
 * The rich transcript plus an input. VS-Code-extension-like, not a terminal.
 *
 * A subagent opens in the same tab, read-only, and a parent lists its
 * subagents in a strip beneath the transcript. See `docs/dev/orchestrator-ui.md`.
 */
export function SessionTab({ agentId }: { agentId: string }) {
  const approve = useApprove();
  const deny = useDeny();
  const answer = useAnswer();
  const say = useSay();
  const run = useRun();
  const setMode = useSetMode();
  const { world } = useWorld();
  const agent = world.agents[agentId];
  const task = agent ? world.tasks[agent.taskId] : undefined;
  const isChild = Boolean(agent?.parent);
  const children = subagentsOf(world, agentId);
  const finished = finishedSubagentsOf(world, agentId);
  const transcript = useAgentStream(agentId);
  const bottom = useRef<HTMLDivElement>(null);
  // The compact wait outlives the render that started it, and an exit never
  // reaches the transcript store the wait subscribes to. So the wait hands
  // back a way to end it, and the effect below calls that when the agent goes.
  const abandonCompact = useRef<((reason: string) => void) | null>(null);
  const exited = agent?.state === 'exited';
  useEffect(() => {
    if (exited) abandonCompact.current?.('The agent exited before it compacted.');
  }, [exited]);
  const count = transcript.items.length;
  const expandedNodeId = useAppStore((s) => s.ui.expandedNodeId);
  // A free agent draws under its own id, a task node under its task's.
  const deferred =
    !!agent?.pendingRequestIds.length && answeredOnCanvas(expandedNodeId, agent.taskId || agent.id);

  useEffect(() => {
    bottom.current?.scrollIntoView?.({ block: 'end' });
  }, [count]);

  if (!agent) return <div className={styles.empty}>Agent {agentId} is gone.</div>;
  // Where the agent runs, what it runs on, how full its context is and what
  // the session has cost so far. A free agent has no task to carry any of
  // this, so its transcript is the only place it can be said. Empty fields
  // drop out, as on the node card.
  const where = world.worktrees[agent.worktreeId];
  const meta = [
    // The qualified folder id, where the node card shows the bare nato word:
    // the card has a project above it to carry the prefix and this line has
    // nothing, so `bravo` alone would not say which project's bravo.
    agent.worktreeId,
    where?.branch || task?.branch || '',
    agent.model,
    contextSize(agent.contextTokens),
    agent.costUsd ? `$${agent.costUsd.toFixed(2)}` : '',
  ].filter(Boolean);
  // Compacting is a turn like any other, so the agent must be free to take
  // one: not mid-turn, not blocked on a person, not gone. The button owns the
  // send itself, so an in-flight one is its business rather than this rule's.
  const canCompact = agent.state === 'idle' && agent.pendingRequestIds.length === 0;
  const compactTitle =
    agent.state === 'exited'
      ? 'The agent has exited.'
      : agent.pendingRequestIds.length > 0
        ? 'The agent is waiting on you. Answer it first.'
        : agent.state !== 'idle'
          ? 'The agent is working. Compacting waits for the turn to end.'
          : 'Compact the conversation, so the session keeps room to work.';
  return (
    <div className={styles.session} data-testid="session-tab">
      <div className={styles.head} data-testid="session-head">
        {/* One row, which a container query breaks into two when the panel is
            dragged narrow. The groups are what it breaks on, so they are spans
            rather than loose children. */}
        <div className={styles.headLine} data-testid="session-head-row">
          <span className={styles.live}>
            <span className={styles.agent}>
              {isChild ? `${agent.id} · ${agent.description}` : agent.id}
            </span>
            <span className={styles.state} data-state={agent.state}>
              {progressOf(task, agent, Object.values(world.attention)).words}
            </span>
            {agent.permissionMode && !isChild && (
              <AppButton
                variant="quiet"
                className={styles.mode}
                title={`Permission mode: ${agent.permissionMode}. Click for ${nextMode(agent.permissionMode)}.`}
                onClick={() =>
                  setMode.mutateAsync({ agentId, mode: nextMode(agent.permissionMode) })
                }
              >
                {agent.permissionMode}
              </AppButton>
            )}
            {agent.waitingOn && <span className={styles.waiting}>{agent.waitingOn}</span>}
          </span>
          {!isChild && (
            <span className={styles.standing}>
              <span className={styles.meta}>{meta.join(' · ')}</span>
              <AppButton
                variant="quiet"
                className={styles.compact}
                disabled={!canCompact}
                title={compactTitle}
                processingChildren="Compacting…"
                onClick={async () => {
                  // `say` resolves when the server accepts the relay, which is
                  // all the relay does. The compaction takes 10s–130s after
                  // that, so the button holds until the boundary says it ended.
                  await say.mutateAsync({ agentId, text: COMPACT_COMMAND });
                  await awaitCompact(agentId, (abandon) => {
                    abandonCompact.current = abandon;
                  });
                }}
              >
                Compact
              </AppButton>
            </span>
          )}
        </div>
      </div>
      <div className={styles.scroll}>
        {transcript.status === 'connecting' && count === 0 && (
          <div className={styles.empty}>Loading the transcript…</div>
        )}
        {transcript.status === 'reconnecting' && (
          <div className={styles.empty} role="status">
            Reconnecting to the transcript…
          </div>
        )}
        {transcript.status === 'ended' && (
          <div className={styles.empty} role="status">
            The server no longer knows this agent.
          </div>
        )}
        <Transcript
          items={transcript.items}
          truncatedBefore={transcript.truncatedBefore}
          deferredRequestIds={deferred ? agent.pendingRequestIds : []}
          handlers={
            isChild
              ? {}
              : {
                  onAnswer: (requestId, answers) =>
                    answer.mutateAsync({ agentId, requestId, answers }),
                  onDecide: (requestId, decision, reason) =>
                    decision === 'approve'
                      ? approve.mutateAsync({ agentId, requestId })
                      : deny.mutateAsync({ agentId, requestId, reason }),
                }
          }
        />
        <div ref={bottom} />
      </div>
      {children.length > 0 && <SubagentStrip agents={children} />}
      {finished.length > 0 && <FinishedSubagents agents={finished} />}
      {!isChild && (
        <MessageInput
          project={agent.project}
          bucket={`agent-${agentId}`}
          disabled={agent.state === 'exited'}
          onSend={(text, attachments) => say.mutateAsync({ agentId, text, attachments })}
          onRun={(command) => run.mutateAsync({ agentId, command })}
        />
      )}
    </div>
  );
}

/**
 * One line per running subagent: a state dot, its description, and what it
 * waits on. A subagent that has finished is not drawn — see `subagentsOf` in
 * `selectors/agents.ts`.
 *
 * A blocked subagent says so here rather than in the parent's stream, so the
 * ask sits beside the subagent that raised it. The decision itself is the
 * parent's — a subagent has no process, so the reply goes to the parent's
 * pipe. See `docs/dev/agent-daemon.md`, "A subagent's permission ask".
 */
function SubagentStrip({ agents }: { agents: Agent[] }) {
  return (
    <div className={styles.subagents} data-testid="subagent-strip">
      {agents.map((child) => (
        <PanelLink key={child.id} tab={sessionTab(child.id)} className={styles.subagent}>
          <span className={styles.dot} data-state={child.state} aria-hidden="true" />
          <span className={styles.subagentId}>{child.id}</span>
          <span className={styles.subagentDescription}>{child.description}</span>
          {child.state.startsWith('awaiting-') && (
            <span className={styles.subagentWaiting} data-testid="subagent-waiting">
              {child.waitingOn || 'needs you'}
            </span>
          )}
        </PanelLink>
      ))}
    </div>
  );
}

/**
 * The way back to a subagent that has finished, folded away. The strip is the
 * only route into a subagent's tab, so hiding a finished one outright would
 * strand its transcript.
 *
 * It stays folded and unadorned: an escape hatch does not compete with the
 * running work above it.
 */
function FinishedSubagents({ agents }: { agents: Agent[] }) {
  return (
    <details className={styles.finished} data-testid="finished-subagents">
      <summary>{agents.length} finished</summary>
      {agents.map((child) => (
        <PanelLink key={child.id} tab={sessionTab(child.id)} className={styles.subagent}>
          <span className={styles.subagentId}>{child.id}</span>
          <span className={styles.subagentDescription}>{child.description}</span>
        </PanelLink>
      ))}
    </details>
  );
}
