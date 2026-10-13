import { useState } from 'react';
import { useWorld } from '../api/useWorld';
import { useSetFeedbackParams } from '../api/liveFeedback';
import type { Worktree } from '../protocol/entities';
import type { WorktreeId } from '../protocol/ids';
import { trackedAgents } from '../selectors/worktrees';
import { ExternalLink } from '../shell/ExternalLink';
import { AppButton } from '../ui/AppButton';
import { EnvControl } from '../worktrees/EnvControl';
import styles from './DevEnvTab.module.css';

/** A dev env tab: one running web service of a worktree, framed. */
export function DevEnvTab({ worktreeId, service }: { worktreeId: WorktreeId; service: string }) {
  const { world } = useWorld();
  const setParams = useSetFeedbackParams(worktreeId, service);
  const worktree = world.worktrees[worktreeId];
  // Every change needs an agent to hear it; the server turns each param off
  // when the last one leaves.
  const params =
    trackedAgents(world, worktreeId).length > 0
      ? (worktree?.liveFeedback?.find((s) => s.service === service)?.params ?? {})
      : {};
  const switches = Object.entries(params)
    .filter(([, param]) => param.type === 'boolean')
    .map(([name, param]) => ({
      name,
      label: param.label,
      on: param.value,
      onToggle: () => setParams.mutateAsync({ [name]: !param.value }),
    }));
  return <DevEnvView worktree={worktree} service={service} switches={switches} />;
}

/** One boolean **Live feedback param** a preview offers: its value, and the press that flips it. */
export interface ParamSwitch {
  name: string;
  label: string;
  on: boolean;
  onToggle: () => Promise<unknown>;
}

/**
 * The tab's body, given its worktree. A service that is gone, stopped or has
 * no URL draws no frame: a frame on a dead port shows the browser's own error
 * page, and the env control is what the reader needs instead.
 */
export function DevEnvView({
  worktree,
  service,
  switches = [],
}: {
  worktree: Worktree | undefined;
  service: string;
  /** Empty where the service exposes no params, or no agent would hear a change. */
  switches?: ParamSwitch[];
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
            {switches.map((param) => (
              <AppButton
                key={param.name}
                variant="quiet"
                role="switch"
                aria-checked={param.on}
                className={styles.param}
                onClick={param.onToggle}
              >
                <input type="checkbox" checked={param.on} readOnly aria-hidden tabIndex={-1} />
                {param.label}
              </AppButton>
            ))}
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
