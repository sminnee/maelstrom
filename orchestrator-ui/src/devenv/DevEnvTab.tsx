import { useState } from 'react';
import { useWorld } from '../api/useWorld';
import type { Worktree } from '../protocol/entities';
import type { WorktreeId } from '../protocol/ids';
import { ExternalLink } from '../shell/ExternalLink';
import { AppButton } from '../ui/AppButton';
import { EnvControl } from '../worktrees/EnvControl';
import styles from './DevEnvTab.module.css';

/** A dev env tab: one running web service of a worktree, framed. */
export function DevEnvTab({ worktreeId, service }: { worktreeId: WorktreeId; service: string }) {
  const { world } = useWorld();
  return <DevEnvView worktree={world.worktrees[worktreeId]} service={service} />;
}

/**
 * The tab's body, given its worktree. A service that is gone, stopped or has
 * no URL draws no frame: a frame on a dead port shows the browser's own error
 * page, and the env control is what the reader needs instead.
 */
export function DevEnvView({
  worktree,
  service,
}: {
  worktree: Worktree | undefined;
  service: string;
}) {
  // Bumped by Reload: a new key is a new frame, loaded from `src` again.
  const [loads, setLoads] = useState(0);
  const live = worktree?.env?.services.find((s) => s.name === service && s.running && s.url);
  if (!worktree)
    return (
      <div className={styles.stopped} data-testid="devenv-tab">
        <p>This worktree is gone.</p>
      </div>
    );
  return (
    <div className={styles.tab} data-testid="devenv-tab">
      <div className={styles.toolbar}>
        <span className={styles.service}>{service}</span>
        {live && (
          <>
            <ExternalLink href={live.url} className={`${styles.url} truncate`}>
              {live.url}
            </ExternalLink>
            <AppButton variant="quiet" onClick={() => setLoads((n) => n + 1)}>
              Reload
            </AppButton>
          </>
        )}
      </div>
      {live ? (
        <iframe
          key={loads}
          className={styles.frame}
          src={live.url}
          title={`${worktree.nato} ${service}`}
        />
      ) : (
        <div className={styles.stopped}>
          <p>{service} is not running.</p>
          {!worktree.isClosed && <EnvControl worktree={worktree} />}
        </div>
      )}
    </div>
  );
}
