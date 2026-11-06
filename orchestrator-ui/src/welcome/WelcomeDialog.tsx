import { useHost } from '../api/host';
import { useSystem, type SystemBody } from '../api/system';
import { tildePath } from '../protocol/paths';
import { useWorld } from '../api/useWorld';
import { useAddProjectDialog, useWelcome } from '../nav/useOverlays';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { Dialog, DialogFooter, DialogHeader } from '../ui/Dialog';
import styles from './WelcomeDialog.module.css';

/** The **Welcome dialog**. `shell/ProjectOverlays.tsx` decides when it opens itself. */
export function WelcomeDialog() {
  const welcome = useWelcome();
  const addProject = useAddProjectDialog();
  const { world } = useWorld();
  const system = useSystem();
  const host = useHost();
  const firstProject = Object.keys(world.projects).length === 0;
  const info = system.data;
  const reachable = host.data?.host?.reachable;

  return (
    <Dialog label="Welcome to maelstrom" onClose={welcome.close} testId="welcome">
      <DialogHeader title="Welcome to maelstrom" onClose={welcome.close} />
      <div className={styles.body}>
        <p className={styles.lede}>
          maelstrom runs coding agents in parallel, each in its own worktree of a project.
        </p>
        {system.isError ? (
          <p className={styles.faint} role="alert">
            Could not read the install: {system.error.message}
          </p>
        ) : (
          <dl className={styles.facts}>
            <dt>Projects</dt>
            <dd className={styles.mono}>{info ? tildePath(info.projectsDir, info.home) : '…'}</dd>
            <dt>Skills</dt>
            <dd>{info ? <Skills info={info} /> : '…'}</dd>
            <dt>Config</dt>
            <dd className={styles.mono}>{info ? tildePath(info.configDir, info.home) : '…'}</dd>
            <dt>Version</dt>
            <dd className={styles.mono} data-testid="welcome-version">
              {info ? versionWords(info) : '…'}
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
  if (info.skills.length === 0)
    return (
      <>
        None linked into {dir}. Run <span className={styles.mono}>mael self setup</span>.
      </>
    );
  return (
    <>
      <span className={styles.mono}>{info.skills.join(', ')}</span>
      <span className={styles.faint}> in </span>
      {dir}
    </>
  );
}

function versionWords({ version }: SystemBody): string {
  if (!version) return 'development build';
  return `${version.tag} (${version.commit.slice(0, 12)}${version.dirty ? ', dirty' : ''})`;
}
