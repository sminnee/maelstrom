import { useWorld } from '../api/useWorld';
import type { GateJob, Worktree } from '../protocol/entities';
import { bytes } from '../protocol/bytes';
import { ago } from '../protocol/time';
import { Dialog, DialogHeader } from '../ui/Dialog';
import { useNow } from '../ui/useNow';
import styles from './GatesDialog.module.css';

/**
 * Whether a job is out of the queue. A stopping or orphaned job is too: it
 * holds a slot, or ran commands that still do.
 */
export const isRunning = (job: GateJob) => job.state !== 'queued';

/** The jobs out of the queue, oldest first, then the queue in its order. */
function splitGateJobs(jobs: GateJob[]): { running: GateJob[]; queued: GateJob[] } {
  const running = jobs.filter(isRunning).sort((a, b) => a.started.localeCompare(b.started));
  const queued = jobs
    .filter((job) => !isRunning(job))
    .sort((a, b) => (a.queuePosition ?? 0) - (b.queuePosition ?? 0));
  return { running, queued };
}

/**
 * The gate jobs on the machine: what runs in tangier's slots, and what waits
 * for one. See DESIGN.md, "Gates chip".
 */
export function GatesDialog({ jobs, onClose }: { jobs: GateJob[]; onClose: () => void }) {
  const { world } = useWorld();
  const now = useNow();
  const { running, queued } = splitGateJobs(jobs);
  const row = (job: GateJob) => (
    <JobRow
      key={job.id}
      job={job}
      worktree={job.worktree ? world.worktrees[job.worktree] : undefined}
      where={
        job.state === 'queued'
          ? `queue ${job.queuePosition ?? '?'} of ${queued.length}`
          : `${job.state === 'running' ? 'started' : job.state}, ${ago(job.started, now)} ago`
      }
    />
  );
  return (
    <Dialog label="Gate jobs" onClose={onClose}>
      <DialogHeader title="Gate jobs" onClose={onClose} />
      <section className={styles.section} aria-labelledby="gates-running">
        <h3 id="gates-running" className={styles.heading}>
          Running
        </h3>
        {running.length ? (
          <ul className={styles.list}>{running.map(row)}</ul>
        ) : (
          <p className={styles.none}>No gate job runs.</p>
        )}
      </section>
      <section className={styles.section} aria-labelledby="gates-queued">
        <h3 id="gates-queued" className={styles.heading}>
          Queued
        </h3>
        {queued.length ? (
          <ul className={styles.list}>{queued.map(row)}</ul>
        ) : (
          <p className={styles.none}>No gate job waits.</p>
        )}
      </section>
    </Dialog>
  );
}

function JobRow({
  job,
  worktree,
  where,
}: {
  job: GateJob;
  worktree: Worktree | undefined;
  where: string;
}) {
  return (
    <li className={styles.job}>
      <div className={styles.line}>
        {/* Mono Means Literal: a path or a worktree name is an identifier. */}
        <span className={`${styles.where} truncate`} title={job.path}>
          {worktree ? `${worktree.project}/${worktree.nato}` : job.path}
        </span>
        <span className={styles.head} title={job.head}>
          {job.head.slice(0, 7)}
          {job.dirty && ' +dirty'}
        </span>
        <span className={styles.meta}>
          {where}
          {job.usage &&
            ` · cpu ${Math.round(job.usage.cpuPercent)}% · mem ${bytes(job.usage.rssBytes)}`}
        </span>
      </div>
      <div className={styles.gates}>
        {job.gates.map((gate) => (
          <span key={gate.name} className={styles.gate} data-state={gate.state} title={gate.state}>
            {gate.name}
          </span>
        ))}
      </div>
    </li>
  );
}
