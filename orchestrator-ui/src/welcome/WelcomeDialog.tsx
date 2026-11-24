import { useQueries } from '@tanstack/react-query';
import { useApi } from '../api/ApiProvider';
import { useHost, useOwnHost } from '../api/host';
import {
  awaitingJob,
  jobStalled,
  systemQuery,
  useFollowedSystem,
  type SystemBody,
} from '../api/system';
import { useLayoutMode } from '../layout/useLayoutMode';
import { isLive } from '../selectors/graph';
import { GatesChip } from '../shell/GatesChip';
import { UsageChips } from '../shell/UsageChips';
import { tildePath } from '../protocol/paths';
import { useWorld } from '../api/useWorld';
import { useAddProjectDialog, useWelcome } from '../nav/useOverlays';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { Dialog, DialogFooter, DialogHeader } from '../ui/Dialog';
import { MachineMetrics } from './MachineMetrics';
import { RemoteHostSection } from './RemoteHostSection';
import { UpdateRow } from './UpdateRow';
import { versionWords } from './versionWords';
import styles from './WelcomeDialog.module.css';

/** The **Welcome dialog**. `shell/ProjectOverlays.tsx` decides when it opens itself. */
export function WelcomeDialog() {
  const welcome = useWelcome();
  const addProject = useAddProjectDialog();
  const { world } = useWorld();
  const { system, startedJob, markStarted } = useFollowedSystem();
  const host = useHost();
  const ownName = useOwnHost().name;
  const remoteHosts = host.data?.host?.remoteHosts ?? [];
  const api = useApi();
  // The main row offers each online remote host that can update. Its body
  // shares the host section's query, so the two read once.
  const online = remoteHosts.filter((h) => h.online).map((h) => h.name);
  const remoteSystems = useQueries({ queries: online.map((name) => systemQuery(api, name)) });
  const remotes = online.filter((_, i) => remoteSystems[i]?.data?.canUpdate === true);
  const firstProject = Object.keys(world.projects).length === 0;
  const info = system.data;
  // A subagent stops and resumes with its parent: count the top-level agents.
  // An agent is a remote host's when its host is one; else it is this server's.
  const remoteNames = new Set(remoteHosts.map((h) => h.name));
  const liveAgents = (hosts: string[]) =>
    Object.values(world.agents).filter(
      (a) => !a.parent && isLive(a) && hosts.includes(remoteNames.has(a.host) ? a.host : ''),
    ).length;
  const reachable = host.data?.host?.reachable;
  const narrow = useLayoutMode() === 'narrow';

  return (
    <Dialog label="Welcome to maelstrom" onClose={welcome.close} testId="welcome">
      <DialogHeader title="Welcome to maelstrom" onClose={welcome.close} />
      <div className={styles.body}>
        <p className={styles.lede}>
          maelstrom runs coding agents in parallel, each in its own worktree of a project.
        </p>
        {/* A failed read keeps the last good body: an update restarts the server. */}
        {system.isError && !info ? (
          <p className={styles.faint} role="alert">
            Could not read the install: {system.error.message}
          </p>
        ) : (
          <dl className={styles.facts}>
            <dt>Projects</dt>
            <dd className={styles.mono}>{info ? tildePath(info.projectsDir, info.home) : '…'}</dd>
            <dt>Skills</dt>
            <dd>{info ? <Skills info={info} /> : '…'}</dd>
            <dt>Practices</dt>
            <dd data-testid="welcome-practices">{info ? <Practices info={info} /> : '…'}</dd>
            <dt>Config</dt>
            <dd className={styles.mono}>{info ? tildePath(info.configDir, info.home) : '…'}</dd>
            <dt>Version</dt>
            <dd className={styles.mono} data-testid="welcome-version">
              {info ? versionWords(info) : '…'}
            </dd>
            <dt>Update</dt>
            <dd>
              {info ? (
                <UpdateRow
                  info={info}
                  ownName={ownName}
                  remotes={remotes}
                  liveAgents={liveAgents}
                  awaiting={awaitingJob(info, startedJob)}
                  stalled={jobStalled(info, startedJob)}
                  startedHere={startedJob !== undefined}
                  onStarted={() => markStarted(info)}
                />
              ) : (
                '…'
              )}
            </dd>
            <dt>Agent host</dt>
            <dd data-testid="welcome-host">
              {reachable === undefined ? '…' : reachable ? 'reachable' : 'not reachable'}
            </dd>
            {/* The narrow bar has room for the agent count only. */}
            {narrow && (
              <>
                <dt>Readings</dt>
                <dd className={styles.readings} data-testid="welcome-readings">
                  <UsageChips />
                  <GatesChip />
                </dd>
              </>
            )}
            <dt>Machine</dt>
            <dd data-testid="welcome-machine">
              <MachineMetrics />
            </dd>
          </dl>
        )}
        {remoteHosts.map((remote) => (
          <RemoteHostSection key={remote.name} host={remote} liveAgents={liveAgents} />
        ))}
      </div>
      <DialogFooter
        aside={
          <AppButton variant="link" onClick={welcome.close}>
            Close
          </AppButton>
        }
      >
        <AppButton
          variant="primary"
          icon={actionIcon('new')}
          onClick={() => {
            welcome.dismiss();
            addProject.open();
          }}
        >
          {firstProject ? 'Add your first project' : 'Add project'}
        </AppButton>
      </DialogFooter>
    </Dialog>
  );
}

function Skills({ info }: { info: SystemBody }) {
  const dir = <span className={styles.mono}>{tildePath(info.skillsDir, info.home)}</span>;
  if (info.skills.length === 0) return <>None in {dir}.</>;
  return (
    <>
      <span className={styles.mono}>{info.skills.join(', ')}</span>
      <span className={styles.faint}> in </span>
      {dir}
    </>
  );
}

/** The optional `minnstack` plugin: installed, or the command that installs it. */
function Practices({ info }: { info: SystemBody }) {
  if (info.practices) return <span className={styles.mono}>minnstack</span>;
  return (
    <>
      Not installed. Run{' '}
      <span className={styles.mono}>claude plugin install minnstack@maelstrom --scope user</span>.
    </>
  );
}
