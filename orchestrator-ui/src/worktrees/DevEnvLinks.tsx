import { Fragment } from 'react';
import type { Worktree } from '../protocol/entities';
import { ExternalLink } from '../shell/ExternalLink';

/** A link to each running web-facing service. */
export function DevEnvLinks({ worktree }: { worktree: Worktree | undefined }) {
  const live = (worktree?.env?.services ?? []).filter((s) => s.running && s.url);
  if (live.length === 0) return null;
  if (live.length === 1) return <ExternalLink href={live[0]!.url}>Dev env</ExternalLink>;
  return (
    <span>
      Dev env:{' '}
      {live.map((s, i) => (
        <Fragment key={s.name}>
          {i > 0 && ' · '}
          <ExternalLink href={s.url}>{s.name}</ExternalLink>
        </Fragment>
      ))}
    </span>
  );
}
