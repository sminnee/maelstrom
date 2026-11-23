import { awaitingJob, jobStalled, useFollowedSystem } from '../api/system';
import type { RemoteHostState } from '../protocol/entities';
import { clockTime } from '../protocol/time';
import { MachineMetrics } from './MachineMetrics';
import { UpdateRow } from './UpdateRow';
import { versionWords } from './versionWords';
import styles from './WelcomeDialog.module.css';

/**
 * One **Remote host** in the **Welcome dialog**: its name and online state,
 * then its Version, its Update row and its Machine charts. The parent reads
 * each through its `/api/hosts/{name}` routes. An offline host shows its
 * heading only.
 */
export function RemoteHostSection({
  host,
  liveAgents,
}: {
  host: RemoteHostState;
  /** The live top-level agents an update of `hosts` stops. */
  liveAgents: (hosts: string[]) => number;
}) {
  const label = `Remote host ${host.name}`;
  return (
    <section className={styles.host} aria-label={label} data-testid={`welcome-host-${host.name}`}>
      <h3 className={styles.hostHead}>
        <span className={styles.mono}>{host.name}</span>{' '}
        <span className={styles.faint}>{host.online ? 'online' : offlineWords(host.since)}</span>
      </h3>
      {host.online && <OnlineHost name={host.name} liveAgents={liveAgents} />}
    </section>
  );
}

function offlineWords(since: string): string {
  const at = clockTime(since, Date.now());
  return at ? `offline since ${at}` : 'offline';
}

function OnlineHost({
  name,
  liveAgents,
}: {
  name: string;
  liveAgents: (hosts: string[]) => number;
}) {
  const { system, startedJob, markStarted } = useFollowedSystem(name);
  const info = system.data;

  if (system.isError && !info)
    return (
      <p className={styles.faint} role="alert">
        Could not read {name}'s install: {system.error.message}
      </p>
    );
  return (
    <dl className={styles.facts}>
      <dt>Version</dt>
      <dd className={styles.mono}>{info ? versionWords(info) : '…'}</dd>
      <dt>Update</dt>
      <dd>
        {info ? (
          <UpdateRow
            info={info}
            host={name}
            liveAgents={liveAgents}
            awaiting={awaitingJob(info, startedJob)}
            stalled={jobStalled(info, startedJob)}
            // A host's new release does not change the bundle this page runs.
            startedHere={false}
            onStarted={() => markStarted(info)}
          />
        ) : (
          '…'
        )}
      </dd>
      <dt>Machine</dt>
      <dd>
        <MachineMetrics host={name} />
      </dd>
    </dl>
  );
}
