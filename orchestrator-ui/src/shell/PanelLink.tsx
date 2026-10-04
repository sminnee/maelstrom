import type { ReactNode } from 'react';
import { useLayoutMode } from '../layout/useLayoutMode';
import { useAppStore } from '../store/store';
import type { MobileScreen } from '../selectors/navStack';
import type { PanelTab } from '../store/uiSlice';
import type { TabOf } from '../selectors/tabs';
import { ExternalLink } from './ExternalLink';
import { OpenInPanelIcon } from './OpenInPanelIcon';
import styles from './link.module.css';

/** A tab the narrow layout also has a screen for. */
type ScreenTab = Exclude<PanelTab, { kind: 'devenv' }>;

/**
 * What a link opens. A dev env tab has no narrow screen, since a phone has no
 * room to frame an app, so it carries the URL the narrow layout links out to.
 */
type Target = { tab: ScreenTab; external?: never } | { tab: TabOf<'devenv'>; external: string };

/** The same destination as a screen the narrow layout can push. */
function screenFor(tab: ScreenTab): MobileScreen {
  switch (tab.kind) {
    case 'session':
      return { kind: 'session', agentId: tab.agentId };
    case 'document':
      return { kind: 'document', documentId: tab.documentId };
    case 'changes':
      return { kind: 'changes', worktreeId: tab.worktreeId };
  }
}

/** The id a tab's href carries: what the panel would show, for a hover or a copied link. */
function hrefFor(tab: PanelTab): string {
  switch (tab.kind) {
    case 'session':
      return `#panel/session/${tab.agentId}`;
    case 'document':
      return `#panel/document/${tab.documentId}`;
    case 'changes':
      return `#panel/changes/${tab.worktreeId}`;
    case 'devenv':
      return `#panel/devenv/${tab.worktreeId}/${tab.service}`;
  }
}

/**
 * A link that opens a session, a document, a worktree's changes or its dev
 * env. Links open more information; buttons act. Every panel link carries the open-in-panel icon so the two
 * are told apart at a glance. The click stops there: a link on a canvas node
 * must not also toggle the node.
 *
 * Where it opens depends on the layout: a panel tab when wide, a pushed
 * screen when narrow, or an external link for a dev env when narrow. Every
 * link in the app goes through here, so one branch carries the whole difference.
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
  const pushScreen = useAppStore((s) => s.pushScreen);
  const narrow = useLayoutMode() === 'narrow';
  if (narrow && tab.kind === 'devenv')
    return (
      <ExternalLink href={external!} className={className} aria-label={ariaLabel}>
        {children}
      </ExternalLink>
    );
  return (
    <a
      href={hrefFor(tab)}
      className={[styles.link, className].filter(Boolean).join(' ')}
      aria-label={ariaLabel}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        if (narrow && tab.kind !== 'devenv') pushScreen(screenFor(tab));
        else openTab(tab);
      }}
    >
      {children}
      {icon && <OpenInPanelIcon className={styles.icon} />}
    </a>
  );
}
