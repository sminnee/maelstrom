import type { ReactNode } from 'react';
import { Link } from 'react-router';
import { useComms, useCommsById } from '../api/comms';
import { useOwnHost } from '../api/host';
import { useWorld } from '../api/useWorld';
import { CommChip } from '../comms/CommChip';
import { useLayoutMode } from '../layout/useLayoutMode';
import type { LocPatch } from '../nav/location';
import { useHrefFor, useLinkState } from '../nav/useNav';
import { resolveLinkTarget, type FoundLink } from '../selectors/linkTargets';
import { changesTab } from '../selectors/tabs';
import { ExternalLink } from '../shell/ExternalLink';
import { GateLozenge } from '../shell/GateLozenge';
import { PanelLink } from '../shell/PanelLink';
import { PrChip } from '../shell/PrChip';
import styles from './LinkCard.module.css';

/**
 * A **Link card**: a `mael:` link in agent markdown, drawn as the item it names.
 *
 * Everything is a `span`: a card sits inside a paragraph or a list item.
 */
export function LinkCard({ uri, label }: { uri: string; label: ReactNode }) {
  const { world, status } = useWorld();
  const commsLoading = useComms().isPending;
  const comms = useCommsById();
  const { idPrefix } = useOwnHost();
  const target = resolveLinkTarget(world, comms, uri, idPrefix);
  // Until the world arrives, nothing is known to be missing.
  if (target.kind === 'missing' && (status === 'loading' || commsLoading))
    return (
      <span className={styles.card} data-testid="link-card" aria-busy="true">
        <span className={styles.missing}>{label}</span>
      </span>
    );
  if (target.kind === 'missing')
    return (
      <span className={styles.card} data-missing="" data-testid="link-card">
        <span className={styles.missing}>{label} cannot be found</span>
      </span>
    );
  return <FoundCard target={target} />;
}

function FoundCard({ target }: { target: FoundLink }) {
  const narrow = useLayoutMode() === 'narrow';
  const { action } = target;
  const body = <CardBody target={target} />;
  const patch = locPatch(target, narrow);
  const to = useHrefFor(patch ?? {});
  const linkState = useLinkState();
  const shared = {
    className: styles.card,
    'data-testid': 'link-card',
    'data-kind': target.kind,
    'data-state': target.state || undefined,
    'data-phase': target.phase ?? undefined,
  };
  if (action.kind === 'external')
    return (
      <span {...shared}>
        <ExternalLink href={action.url} className={styles.link}>
          {body}
        </ExternalLink>
      </span>
    );
  if (action.kind === 'tab' || (action.kind === 'worktree' && narrow))
    return (
      <span {...shared}>
        <PanelLink
          tab={action.kind === 'tab' ? action.tab : changesTab(action.worktreeId)}
          className={styles.link}
        >
          {body}
        </PanelLink>
      </span>
    );
  return (
    <span {...shared}>
      <Link to={to} state={linkState} className={styles.link} onClick={(e) => e.stopPropagation()}>
        {body}
      </Link>
    </span>
  );
}

/** The location a card opens, for the targets a panel tab or a URL out does not open. */
function locPatch(target: FoundLink, narrow: boolean): LocPatch | null {
  const { action } = target;
  if (action.kind === 'card') return { card: action.card };
  if (action.kind === 'comm') return { commDetail: action.commId };
  if (action.kind === 'worktree' && !narrow)
    return { card: { kind: 'worktree', id: action.worktreeId } };
  return null;
}

function CardBody({ target }: { target: FoundLink }) {
  return (
    <span className={styles.body}>
      <span className={styles.head}>
        {target.state && <span className={styles.dot} aria-hidden />}
        <span className={styles.title}>{target.title}</span>
        {target.detail && <span className={styles.detail}>{target.detail}</span>}
      </span>
      <span className={styles.meta}>
        {target.comm && <CommChip comm={target.comm} interactive={false} />}
        {target.pr && <PrChip pr={target.pr} link={false} />}
        {target.gate && <GateLozenge gate={target.gate} interactive={false} />}
        {target.words && <span className={styles.words}>{target.words}</span>}
        <span className={styles.action}>{target.actionLabel}</span>
      </span>
    </span>
  );
}
