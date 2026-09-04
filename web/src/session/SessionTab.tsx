import { useEffect, useRef } from 'react';
import { useAnswer, useApprove, useDeny, useSay, useSetMode } from '../api/agents';
import { useWorld } from '../api/useWorld';
import { useAgentStream } from '../live/useAgentStream';
import type { Agent } from '../protocol/entities';
import { nextMode } from '../protocol/modes';
import { subagentsOf } from '../selectors/agents';
import { describeState } from '../selectors/status';
import { sessionTab } from '../selectors/tabs';
import { answeredOnCanvas } from '../selectors/transcript';
import { PanelLink } from '../shell/PanelLink';
import { useAppStore } from '../store/store';
import { AppButton } from '../ui/AppButton';
import { MessageInput } from './MessageInput';
import { Transcript } from './Transcript';
import styles from './SessionTab.module.css';

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
  const setMode = useSetMode();
  const { world } = useWorld();
  const agent = world.agents[agentId];
  const task = agent ? world.tasks[agent.taskId] : undefined;
  const isChild = Boolean(agent?.parent);
  const children = subagentsOf(world, agentId);
  const transcript = useAgentStream(agentId);
  const bottom = useRef<HTMLDivElement>(null);
  const count = transcript.items.length;
  const expandedNodeId = useAppStore((s) => s.ui.expandedNodeId);
  // A free agent draws under its own id, a task node under its task's.
  const deferred =
    agent?.pendingRequestId != null && answeredOnCanvas(expandedNodeId, agent.taskId || agent.id);

  useEffect(() => {
    bottom.current?.scrollIntoView?.({ block: 'end' });
  }, [count]);

  if (!agent) return <div className={styles.empty}>Agent {agentId} is gone.</div>;
  return (
    <div className={styles.session}>
      <div className={styles.head}>
        <span className={styles.agent}>
          {isChild ? `${agent.id} · ${agent.description}` : agent.id}
        </span>
        <span className={styles.state} data-state={agent.state}>
          {describeState(task, agent)}
        </span>
        {agent.permissionMode && !isChild && (
          <AppButton
            variant="quiet"
            className={styles.mode}
            title={`Permission mode: ${agent.permissionMode}. Click for ${nextMode(agent.permissionMode)}.`}
            onClick={() => setMode.mutateAsync({ agentId, mode: nextMode(agent.permissionMode) })}
          >
            {agent.permissionMode}
          </AppButton>
        )}
        {agent.waitingOn && <span className={styles.waiting}>{agent.waitingOn}</span>}
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
          deferredRequestId={deferred ? agent.pendingRequestId : null}
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
      {!isChild && (
        <MessageInput
          disabled={agent.state === 'exited'}
          onSend={(text) => say.mutateAsync({ agentId, text })}
        />
      )}
    </div>
  );
}

/** One line per subagent: a state dot, its description, and its summary once it has ended. */
function SubagentStrip({ agents }: { agents: Agent[] }) {
  return (
    <div className={styles.subagents} data-testid="subagent-strip">
      {agents.map((child) => (
        <PanelLink key={child.id} tab={sessionTab(child.id)} className={styles.subagent}>
          <span className={styles.dot} data-state={child.state} aria-hidden="true" />
          <span className={styles.subagentId}>{child.id}</span>
          <span className={styles.subagentDescription}>{child.description}</span>
          {child.state === 'exited' && child.lastMessage && (
            <span className={styles.subagentSummary}>{child.lastMessage}</span>
          )}
        </PanelLink>
      ))}
    </div>
  );
}
