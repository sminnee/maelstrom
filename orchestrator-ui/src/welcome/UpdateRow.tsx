import { useState } from 'react';
import { useStartUpdate, type SystemBody } from '../api/system';
import { tildePath } from '../protocol/paths';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { SplitButton, type SplitOption } from '../ui/SplitButton';
import styles from './WelcomeDialog.module.css';

/**
 * The **Welcome dialog**'s update row: start an **Update job**, and follow it
 * through the orchestrator restart it causes.
 *
 * `host` names the **Remote host** the row is for; with none, the row is this
 * server's. This server's row can also start the job on each host of
 * `remotes` first: each host's job runs before this server's, whose restart
 * cuts the page's link.
 *
 * `awaiting` is true from a started job until its record replaces the old
 * one: the dialog polls the install through that gap. `stalled` says the
 * job never wrote its record. After a job this page started installs a new
 * release, the page still runs the old bundle, so the row offers a reload.
 */
export function UpdateRow({
  info,
  host,
  ownName = '',
  remotes = [],
  liveAgents,
  awaiting,
  stalled,
  startedHere,
  onStarted,
}: {
  info: SystemBody;
  /** The remote host this row is for. */
  host?: string;
  /** This server's name, for the labels of this server's row. */
  ownName?: string;
  /** The online remote hosts that can update, which this server's row offers too. */
  remotes?: string[];
  /** The live top-level agents an update of `hosts` stops. `''` is this server. */
  liveAgents: (hosts: string[]) => number;
  awaiting: boolean;
  stalled: boolean;
  /** This page started the job, so it still runs the old UI bundle. */
  startedHere: boolean;
  onStarted: () => void;
}) {
  /** The hosts the confirm asks about, in the order their jobs start. `''` is this server. */
  const [confirming, setConfirming] = useState<string[] | null>(null);
  const start = useStartUpdate();
  const record = info.update;
  const own = ownName || 'this server';
  const log = record ? (
    <span className={styles.mono}>{tildePath(record.log, info.home)}</span>
  ) : null;

  if (awaiting || record?.state === 'running')
    return (
      <div className={styles.update} data-testid="welcome-update">
        <span>Updating…</span>
        <span className={styles.faint}>
          {host ? `${host}'s orchestrator restarts` : 'The orchestrator restarts'} during the
          update.{log && <> The log is {log}.</>}
        </span>
      </div>
    );

  if (confirming) {
    const count = liveAgents(confirming);
    return (
      <div className={styles.update} data-testid="welcome-update">
        <span>
          {restartWords(confirming, own)}{' '}
          {count === 1
            ? '1 live agent stops. It resumes after the restart.'
            : `${count} live agents stop. They resume after the restart.`}
        </span>
        <div className={styles.actions}>
          <AppButton
            variant="primary"
            icon={actionIcon('update')}
            processingChildren="Starting…"
            errorChildren={(err) => (err instanceof Error ? err.message : 'Failed')}
            onClick={async () => {
              await start.mutateAsync(confirming);
              setConfirming(null);
              onStarted();
            }}
          >
            Upgrade
          </AppButton>
          <AppButton icon={actionIcon('close')} onClick={() => setConfirming(null)}>
            Cancel
          </AppButton>
        </div>
      </div>
    );
  }

  const ask = (hosts: string[]) => async () => setConfirming(hosts);
  const options: SplitOption[] = host
    ? [{ label: `Upgrade ${host}`, buttonLabel: 'Upgrade', run: ask([host]) }]
    : remotes.length > 0
      ? [
          {
            label: `Upgrade ${[own, ...remotes].join(', ')}`,
            buttonLabel: 'Upgrade',
            isDefault: true,
            run: ask([...remotes, '']),
          },
          { label: `Upgrade ${own} only`, run: ask(['']) },
        ]
      : [{ label: 'Upgrade', run: ask(['']) }];

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
        <SplitButton
          options={options.map((o) => ({ ...o, icon: actionIcon('update') }))}
          menuLabel="More upgrade actions"
          disabled={!info.canUpdate}
        />
        <span className={styles.faint}>
          {info.canUpdate ? `from the ${info.updateChannel} channel` : 'development build'}
        </span>
      </div>
    </div>
  );
}

/** What the confirm says the jobs of `hosts` restart. `''` is this server, named `own`. */
function restartWords(hosts: string[], own: string): string {
  const [only] = hosts;
  if (hosts.length === 1 && only === '')
    return 'The update restarts the orchestrator and the agent daemon.';
  if (hosts.length === 1) return `The update restarts ${only}'s orchestrator and agent daemon.`;
  const names = hosts.map((h) => h || own).join(', then ');
  return `The update restarts the orchestrator and the agent daemon on ${names}.`;
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
