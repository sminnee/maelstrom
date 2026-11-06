import { useEffect, useState } from 'react';
import { useHost } from '../api/host';
import {
  JOB_START_MS,
  awaitingJob,
  jobStalled,
  useSystem,
  type StartedJob,
  type SystemBody,
} from '../api/system';
import { isLive } from '../selectors/graph';
import { tildePath } from '../protocol/paths';
import { useWorld } from '../api/useWorld';
import { useAddProjectDialog, useWelcome } from '../nav/useOverlays';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { Dialog, DialogFooter, DialogHeader } from '../ui/Dialog';
import { UpdateRow } from './UpdateRow';
import styles from './WelcomeDialog.module.css';

/** The **Welcome dialog**. `shell/ProjectOverlays.tsx` decides when it opens itself. */
export function WelcomeDialog() {
  const welcome = useWelcome();
  const addProject = useAddProjectDialog();
  const { world } = useWorld();
  const [startedJob, setStartedJob] = useState<StartedJob>();
  const system = useSystem({ startedJob });
  const host = useHost();
  const firstProject = Object.keys(world.projects).length === 0;
  const info = system.data;
  // A subagent stops and resumes with its parent: count the top-level agents.
  const liveAgents = Object.values(world.agents).filter((a) => !a.parent && isLive(a)).length;
  useEffect(() => {
    if (!startedJob || startedJob.stalled) return;
    const timer = setTimeout(
      () => setStartedJob((job) => job && { ...job, stalled: true }),
      JOB_START_MS,
    );
    return () => clearTimeout(timer);
  }, [startedJob]);
  const reachable = host.data?.host?.reachable;

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
                  liveAgents={liveAgents}
                  awaiting={awaitingJob(info, startedJob)}
                  stalled={jobStalled(info, startedJob)}
                  startedHere={startedJob !== undefined}
                  onStarted={() => setStartedJob({ after: info.update?.startedAt ?? null })}
                />
              ) : (
                '…'
              )}
            </dd>
            <dt>Agent host</dt>
            <dd data-testid="welcome-host">
              {reachable === undefined ? '…' : reachable ? 'reachable' : 'not reachable'}
            </dd>
          </dl>
        )}
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

function versionWords({ version }: SystemBody): string {
  if (!version) return 'development build';
  return `${version.tag} (${version.commit.slice(0, 12)}${version.dirty ? ', dirty' : ''})`;
}
