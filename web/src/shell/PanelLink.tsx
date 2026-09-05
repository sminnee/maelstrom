import type { ReactNode } from 'react';
import { useLayoutMode } from '../layout/useLayoutMode';
import { useAppStore } from '../store/store';
import type { MobileScreen } from '../selectors/navStack';
import type { PanelTab } from '../store/uiSlice';
import { OpenInPanelIcon } from './OpenInPanelIcon';
import styles from './PanelLink.module.css';

/** The same destination as a screen the narrow layout can push. */
function screenFor(tab: PanelTab): MobileScreen {
  switch (tab.kind) {
    case 'session':
      return { kind: 'session', agentId: tab.agentId };
    case 'document':
      return { kind: 'document', documentId: tab.documentId };
  }
}

/** The id a tab's href carries: what the panel would show, for a hover or a copied link. */
function hrefFor(tab: PanelTab): string {
  switch (tab.kind) {
    case 'session':
      return `#panel/session/${tab.agentId}`;
    case 'document':
      return `#panel/document/${tab.documentId}`;
  }
}

/**
 * A link that opens a session or a document. Links open more information;
 * buttons act. Every panel link carries the open-in-panel icon so the two
 * are told apart at a glance. The click stops there: a link on a canvas node
 * must not also toggle the node.
 *
 * Where it opens depends on the layout: a panel tab when wide, a pushed
 * screen when narrow. Every link in the app goes through here, so one branch
 * carries the whole difference.
 */
export function PanelLink({
  tab,
  children,
  className,
  icon = true,
  'aria-label': ariaLabel,
}: {
  tab: PanelTab;
  children: ReactNode;
  className?: string;
  /** False for a badge that carries its own glyph. */
  icon?: boolean;
  'aria-label'?: string;
}) {
  const openTab = useAppStore((s) => s.openTab);
  const pushScreen = useAppStore((s) => s.pushScreen);
  const narrow = useLayoutMode() === 'narrow';
  return (
    <a
      href={hrefFor(tab)}
      className={[styles.link, className].filter(Boolean).join(' ')}
      aria-label={ariaLabel}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        if (narrow) pushScreen(screenFor(tab));
        else openTab(tab);
      }}
    >
      {children}
      {icon && <OpenInPanelIcon className={styles.icon} />}
    </a>
  );
}
