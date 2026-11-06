import { useState } from 'react';
import { useStartUpdate, type SystemBody } from '../api/system';
import { tildePath } from '../protocol/paths';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import styles from './WelcomeDialog.module.css';

/**
 * The **Welcome dialog**'s update row: start an **Update job**, and follow it
 * through the orchestrator restart it causes.
 *
 * `awaiting` is true from a started job until its record replaces the old
 * one: the dialog polls the install through that gap. `stalled` says the
 * job never wrote its record. After a job this page started installs a new
 * release, the page still runs the old bundle, so the row offers a reload.
 */
export function UpdateRow({
  info,
  liveAgents,
  awaiting,
  stalled,
  startedHere,
  onStarted,
}: {
  info: SystemBody;
  liveAgents: number;
  awaiting: boolean;
  stalled: boolean;
  /** This page started the job, so it still runs the old UI bundle. */
  startedHere: boolean;
  onStarted: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const start = useStartUpdate();
  const record = info.update;
  const log = record ? (
    <span className={styles.mono}>{tildePath(record.log, info.home)}</span>
  ) : null;

  if (awaiting || record?.state === 'running')
    return (
      <div className={styles.update} data-testid="welcome-update">
        <span>Updating…</span>
        <span className={styles.faint}>
          The orchestrator restarts during the update.{log && <> The log is {log}.</>}
        </span>
      </div>
    );

  if (confirming)
    return (
      <div className={styles.update} data-testid="welcome-update">
        <span>
          The update restarts the orchestrator and the agent daemon.{' '}
          {liveAgents === 1
            ? '1 live agent stops. It resumes after the restart.'
            : `${liveAgents} live agents stop. They resume after the restart.`}
        </span>
        <div className={styles.actions}>
          <AppButton
            variant="primary"
            icon={actionIcon('update')}
            processingChildren="Starting…"
            errorChildren={(err) => (err instanceof Error ? err.message : 'Failed')}
            onClick={async () => {
              await start.mutateAsync();
              setConfirming(false);
              onStarted();
            }}
          >
            Update
          </AppButton>
          <AppButton icon={actionIcon('close')} onClick={() => setConfirming(false)}>
            Cancel
          </AppButton>
        </div>
      </div>
    );

  return (
    <div className={styles.update} data-testid="welcome-update">
      {stalled ? (
        <span role="alert">
          The update job did not start. The log is{' '}
          <span className={styles.mono}>
            {tildePath(`${info.configDir}/logs/update.log`, info.home)}
          </span>
          .
        </span>
      ) : (
        record && <LastUpdate record={record} log={log} />
      )}
      {startedHere && record?.state === 'succeeded' && record.fromTag !== record.toTag && (
        <div className={styles.actions}>
          <AppButton
            variant="primary"
            icon={actionIcon('refresh')}
            onClick={() => window.location.reload()}
          >
            Reload
          </AppButton>
          <span className={styles.faint}>to load the new release's UI</span>
        </div>
      )}
      <div className={styles.actions}>
        <AppButton
          icon={actionIcon('update')}
          disabled={!info.canUpdate}
          onClick={() => setConfirming(true)}
        >
          Update maelstrom
        </AppButton>
        <span className={styles.faint}>
          {info.canUpdate ? `from the ${info.updateChannel} channel` : 'development build'}
        </span>
      </div>
    </div>
  );
}

function LastUpdate({
  record,
  log,
}: {
  record: NonNullable<SystemBody['update']>;
  log: React.ReactNode;
}) {
  switch (record.state) {
    case 'succeeded':
      return (
        <span>
          {record.fromTag && record.fromTag === record.toTag
            ? `Already up to date (${record.toTag})`
            : `Updated to ${record.toTag ?? 'a new release'}`}
        </span>
      );
    case 'failed':
      return (
        <span role="alert">
          The last update failed (exit {record.exitCode}). The log is {log}.
        </span>
      );
    case 'interrupted':
      return <span role="alert">The last update stopped before it ended. The log is {log}.</span>;
    case 'running':
      return null;
  }
}
