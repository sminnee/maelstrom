import { useEffect, useState } from 'react';
import { useStop } from '../api/agents';
import { useRemoveFromDesk } from '../api/desk';
import { useMilestones } from '../api/milestones';
import { useLaunch, useSetStatus, useTask } from '../api/tasks';
import { useWorld } from '../api/useWorld';
import { useAppStore } from '../store/store';
import { useAgentStream } from '../live/useAgentStream';
import { DecisionCard } from '../decisions/DecisionCard';
import { Markdown } from '../markdown/Markdown';
import { deskIdForAgent, deskIdForTask } from '../protocol/deskId';
import { modelLabel } from '../protocol/models';
import { driftFixLabel, driftSentence } from '../protocol/progress';
import type { GraphNode } from '../selectors/graph';
import { isLive, nodeIdLine, nodeTitle } from '../selectors/graph';
import { describeDocumentStatus } from '../selectors/status';
import { documentTab, sessionTab } from '../selectors/tabs';
import { toolCallTitle } from '../session/toolCards';
import { ExternalLink } from '../shell/ExternalLink';
import { PanelLink } from '../shell/PanelLink';
import { PrChip } from '../shell/PrChip';
import { phaseLabel } from '../protocol/phase';
import { ago, clockTime, silentFor } from '../protocol/time';
import { contextFigure } from '../protocol/tokens';
import { useNow } from '../ui/useNow';
import { AppButton } from '../ui/AppButton';
import { useExpandableClamp } from '../ui/useExpandableClamp';
import { StatusPicker } from '../ui/StatusPicker';
import styles from './NodeCard.module.css';

/**
 * How long a working agent may be silent before its age is worth colouring.
 * One step, not a scale: past this the age is the signal, not the message.
 */
const SILENT_MS = 10 * 60_000;

/**
 * What an expanded node says: its title, identity, status, drift, brief, the
 * decision it waits on, and its links and commands.
 *
 * The content only — each layout supplies its own surface around it, so the
 * two cannot drift on what a node says.
 *
 * `onDone` is called when a command has taken the node off the surface — a
 * dismiss or a remove from the desk. The canvas collapses the card; the narrow
 * layout goes back to the deck list.
 */
export function NodeCardBody({
  node,
  closeControl,
  onDone,
}: {
  node: GraphNode;
  /** What closes the surface, drawn at the top right of the header. */
  closeControl?: React.ReactNode;
  onDone: () => void;
}) {
  const { world } = useWorld();
  const transcript = useAgentStream(node.agent?.id ?? null);
  const milestones = useMilestones(node.agent?.id ?? null);
  const launch = useLaunch();
  const stop = useStop();
  const setStatus = useSetStatus();
  const removeFromDesk = useRemoveFromDesk();
  const editTask = useAppStore((s) => s.setEditingTask);
  const [picking, setPicking] = useState(false);
  const { task, agent, worktree } = node;
  // The list holds slim rows, so the brief comes from the task's detail.
  const detail = useTask(task?.id ?? null);
  const brief = detail.data?.content.trim() ?? '';

  const {
    expanded: expandedContent,
    collapse,
    bodyProps: briefProps,
  } = useExpandableClamp([brief]);

  // A plan document is found by its task; a free agent has no task, so a
  // document it tagged is found by its agent alone.
  const documents = Object.values(world.documents).filter(
    (d) => (task && d.taskId === task.id) || (agent && d.agentId === agent.id),
  );
  // The worktree is where the agent runs, so its branch beats the frontmatter.
  const where = worktree ?? (agent ? world.worktrees[agent.worktreeId] : undefined);
  const meta = [
    where?.branch || task?.branch || '',
    where?.nato || (agent ? agent.worktreeId : ''),
    modelLabel(agent?.model || task?.model || ''),
    agent?.permissionMode || '',
    agent?.costUsd ? `$${agent.costUsd.toFixed(2)}` : '',
  ].filter(Boolean);
  // A stopped env, or a worktree with no web-facing port, draws nothing.
  const appUrl = where?.appRunning && where.appUrl ? where.appUrl : '';
  const title = nodeTitle(node);
  const deciding = !!agent && agent.pendingRequestIds.length > 0;
  const running = [...transcript.items]
    .reverse()
    .find((i) => i.type === 'tool_call' && i.status === 'running');
  // The agent's own summary of the work when it wrote one, else whatever prose
  // ended its last turn. An agent that never notes reads as it did before.
  const note = agent?.lastNote ?? '';
  const now = note || (agent?.lastMessage ?? '');
  // Deliberately the message, not the note: silence means *said nothing*, and a
  // note is not speech. Dating this from the note would make an agent that
  // noted once look alive for ever — the failure this display exists to show.
  const spokeAt = agent?.lastMessageAt ?? '';
  const clock = useNow();
  const age = ago(spokeAt, clock);
  // An idle agent's silence is not alarming; a working one's is the stall this
  // display exists to show.
  const quiet = silentFor(spokeAt, clock);
  const silent = agent?.state === 'processing' && quiet !== null && quiet >= SILENT_MS;
  // The last stage only. See `docs/dev/orchestrator-ui.md`.
  const stage = milestones.data?.stages.at(-1);
  const stageAge = stage ? ago(stage.at, clock) : '';
  // The ledger moves no world entity, so no change notice fires: a bar
  // arriving is the only signal there is a newer stage to read.
  const barsSeen = transcript.items.filter((i) => i.type === 'milestone').length;
  const refetchMilestones = milestones.refetch;
  useEffect(() => {
    if (barsSeen > 0) void refetchMilestones();
  }, [barsSeen, refetchMilestones]);

  return (
    <>
      <header className={styles.header}>
        <div className={styles.titleBlock}>
          <h2 className={styles.title}>{title}</h2>
          <div className={styles.idLine}>
            {node.showProject && task && <span className={styles.project}>{task.project}</span>}
            <span className={styles.id}>{nodeIdLine(node)}</span>
            {node.phase && <span className={styles.phase}>{phaseLabel(node.phase)}</span>}
          </div>
          {meta.length > 0 && (
            <div className={styles.meta} data-testid="node-meta">
              {meta.join(' · ')}
            </div>
          )}
        </div>
        {closeControl}
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

      {stage && (
        <div
          className={styles.stage}
          data-testid="milestone-band"
          data-recognised={stage.recognised}
        >
          <span className={styles.stageName}>{stage.name}</span>
          {!stage.recognised && ' (?)'}
          {stage.delta_tokens > 0 && ` · ${contextFigure(stage.delta_tokens)}`}
          {stage.cost_delta > 0 && ` · $${stage.cost_delta.toFixed(2)}`}
          {stageAge && (
            <>
              {' · '}
              <time dateTime={stage.at} title={clockTime(stage.at, clock)}>
                {stageAge} ago
              </time>
            </>
          )}
        </div>
      )}

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
        <div className={styles.content} data-testid="task-content" data-expanded={expandedContent}>
          <div className={styles.briefBox} {...briefProps}>
            <Markdown source={brief} className={styles.brief} />
          </div>
          {expandedContent && (
            <AppButton
              variant="link"
              className={styles.more}
              aria-controls={briefProps.id}
              onClick={collapse}
            >
              Show less
            </AppButton>
          )}
        </div>
      )}

      {deciding && agent ? (
        <DecisionCard agent={agent} />
      ) : (
        (now || running) && (
          <div className={styles.now} data-silent={(age && silent) || undefined}>
            <div className={styles.nowBand}>
              <span className={styles.nowHead}>Now</span>
              {age && (
                <time
                  className={styles.nowAge}
                  data-testid="now-age"
                  dateTime={spokeAt}
                  title={clockTime(spokeAt, clock)}
                >
                  {age} ago
                </time>
              )}
            </div>
            <span className={styles.nowText} data-note={now === note && note ? '' : undefined}>
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
        <div className={styles.actions} data-testid="node-actions">
          {agent && <PanelLink tab={sessionTab(agent.id)}>Session</PanelLink>}
          <PrChip worktree={where} size="large" />
          {appUrl && <ExternalLink href={appUrl}>Dev env</ExternalLink>}
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
                onDone();
              }}
            >
              Dismiss
            </AppButton>
          )}
          {node.kind === 'task' && task && (
            <AppButton variant="quiet" onClick={() => editTask(task.id)}>
              Edit task
            </AppButton>
          )}
          {/* Hidden rather than disabled, unlike Dismiss above: a task keeps
              its task list row as the other way off the desk. */}
          {node.kind === 'task' && task && !isLive(agent) && (
            <AppButton
              variant="quiet"
              onClick={async () => {
                await removeFromDesk.mutateAsync({ id: deskIdForTask(task.id) });
                onDone();
              }}
            >
              Remove from desk
            </AppButton>
          )}
          {agent && isLive(agent) && (
            /* Terminate ends the process; the session tab's Stop only abandons
               the turn — see CONTEXT.md, "Interrupt". */
            <AppButton variant="quiet" onClick={() => stop.mutateAsync({ agentId: agent.id })}>
              Terminate
            </AppButton>
          )}
        </div>
        {documents.length > 0 && (
          <div className={styles.documents} data-testid="node-documents">
            {documents.map((d) => (
              <PanelLink key={d.id} tab={documentTab(d.id)}>
                {d.title} v{d.version} · {describeDocumentStatus(d.status)}
              </PanelLink>
            ))}
          </div>
        )}
      </footer>
    </>
  );
}
