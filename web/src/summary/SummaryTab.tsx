import { openAttentionFor, agentForTask } from '../selectors/graph';
import { useAppStore } from '../store/store';
import { documentTab } from '../selectors/tabs';
import { QuickActions } from './QuickActions';
import styles from './SummaryTab.module.css';

/** Status, what the agent waits on, its last words, its documents and the quick actions. */
export function SummaryTab({ taskId }: { taskId: string }) {
  const world = useAppStore((s) => s.world);
  const transcripts = useAppStore((s) => s.transcripts);
  const task = world.tasks[taskId];
  if (!task) return <div className={styles.empty}>Task {taskId} is gone.</div>;
  const agent = agentForTask(world, taskId);
  const attention = openAttentionFor(world, task, agent);
  const documents = Object.values(world.documents).filter((d) => d.taskId === taskId);
  const messages = (agent ? (transcripts[agent.id]?.items ?? []) : [])
    .filter((i) => i.type === 'message' && i.role === 'assistant')
    .slice(-3);

  return (
    <div className={styles.summary} data-phase={task.phase}>
      <header className={styles.header}>
        <div className={styles.id}>
          {task.id} · <span className={styles.phase}>{task.phase}</span> · {task.status}
        </div>
        <h2 className={styles.title}>{task.title}</h2>
        <div className={styles.meta}>
          {task.branch}
          {agent
            ? ` · ${world.worktrees[agent.worktreeId]?.nato ?? agent.worktreeId} · ${agent.model}`
            : ''}
          {agent?.costUsd ? ` · $${agent.costUsd.toFixed(2)}` : ''}
        </div>
      </header>

      <section className={styles.section}>
        <h3>State</h3>
        <div className={styles.state} data-state={agent?.state ?? 'none'}>
          {agent ? agent.state : task.actionable ? 'queued, actionable' : 'queued'}
          {agent?.exitCode !== null && agent?.exitCode !== undefined
            ? ` (exit ${agent.exitCode})`
            : ''}
        </div>
        {agent?.waitingOn && <div className={styles.waiting}>{agent.waitingOn}</div>}
        {attention.map((a) => (
          <div key={a.id} className={styles.attention}>
            ⚠ {a.summary}
          </div>
        ))}
      </section>

      <QuickActions task={task} agent={agent} attention={attention} />

      {messages.length > 0 && (
        <section className={styles.section}>
          <h3>Last said</h3>
          {messages.map((m) => (
            <blockquote key={m.id} className={styles.quote}>
              {m.type === 'message' ? m.markdown : ''}
            </blockquote>
          ))}
        </section>
      )}

      {documents.length > 0 && (
        <section className={styles.section}>
          <h3>Documents</h3>
          <ul className={styles.docs}>
            {documents.map((d) => (
              <li key={d.id}>
                <DocumentLink id={d.id} title={d.title} version={d.version} status={d.status} />
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function DocumentLink(props: { id: string; title: string; version: number; status: string }) {
  const openTab = useAppStore((s) => s.openTab);
  return (
    <button type="button" className={styles.link} onClick={() => openTab(documentTab(props.id))}>
      {props.title} v{props.version} · {props.status}
    </button>
  );
}
