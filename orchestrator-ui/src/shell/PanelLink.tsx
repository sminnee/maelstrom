import type { ReactNode } from 'react';
import { Link } from 'react-router';
import { useLayoutMode } from '../layout/useLayoutMode';
import { IN_APP, useHrefFor } from '../nav/useNav';
import { useAppStore } from '../store/store';
import type { PanelTab } from '../store/uiSlice';
import type { TabOf } from '../selectors/tabs';
import { ExternalLink } from './ExternalLink';
import { actionIcon } from '../ui/actionIcons';
import styles from './link.module.css';

/** A tab the narrow layout also has a screen for. */
type ScreenTab = Exclude<PanelTab, { kind: 'devenv' }>;

/**
 * What a link opens. A dev env tab has no narrow screen, since a phone has no
 * room to frame an app, so it carries the URL the narrow layout links out to.
 */
type Target = { tab: ScreenTab; external?: never } | { tab: TabOf<'devenv'>; external: string };

/**
 * A link that opens a session, a document, a worktree's changes or its dev
 * env. Links open more information; buttons act. Every panel link carries the open-in-panel icon so the two
 * are told apart at a glance. The click stops there: a link on a canvas node
 * must not also toggle the node.
 *
 * Its href is the current location with the tab as its `panel`, so it opens in a new window
 * too. The location draws as a panel tab when wide and as a screen when narrow; a dev env
 * links out of the app when narrow. Every link in the app goes through here, so one branch
 * carries the whole difference.
 */
export function PanelLink({
  tab,
  external,
  children,
  className,
  icon = true,
  'aria-label': ariaLabel,
}: Target & {
  children: ReactNode;
  className?: string;
  /** False for a badge that carries its own glyph. */
  icon?: boolean;
  'aria-label'?: string;
}) {
  const openTab = useAppStore((s) => s.openTab);
  const narrow = useLayoutMode() === 'narrow';
  const to = useHrefFor({ panel: tab });
  if (narrow && tab.kind === 'devenv')
    return (
      <ExternalLink href={external!} className={className} aria-label={ariaLabel}>
        {children}
      </ExternalLink>
    );
  return (
    <Link
      to={to}
      state={IN_APP}
      className={[styles.link, className].filter(Boolean).join(' ')}
      aria-label={ariaLabel}
      onClick={(e) => {
        e.stopPropagation();
        if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey || e.button !== 0) return;
        // The location may name the tab already, while the panel is hidden or the tab is
        // split: a link must show what it opened.
        if (!narrow) openTab(tab);
      }}
    >
      {children}
      {icon && actionIcon('openInPanel', styles.icon)}
    </Link>
  );
}
