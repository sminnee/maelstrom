import { Fragment, type ReactNode } from 'react';
import type { Worktree } from '../protocol/entities';
import { devEnvTab } from '../selectors/tabs';
import { PanelLink } from '../shell/PanelLink';

/** A link to each running web-facing service. */
export function DevEnvLinks({ worktree }: { worktree: Worktree | undefined }) {
  const live = (worktree?.env?.services ?? []).filter((s) => s.running && s.url);
  if (!worktree || live.length === 0) return null;
  const link = (service: (typeof live)[number], children: ReactNode) => (
    <PanelLink tab={devEnvTab(worktree.id, service.name)} external={service.url}>
      {children}
    </PanelLink>
  );
  if (live.length === 1) return link(live[0]!, 'Dev env');
  return (
    <span>
      Dev env:{' '}
      {live.map((s, i) => (
        <Fragment key={s.name}>
          {i > 0 && ' · '}
          {link(s, s.name)}
        </Fragment>
      ))}
    </span>
  );
}
