import type { ReactNode } from 'react';
import { useAppStore } from '../store/store';
import type { PanelTab } from '../store/uiSlice';
import { OpenInPanelIcon } from './OpenInPanelIcon';
import styles from './PanelLink.module.css';

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
 * A link that opens a tab in the right panel. Links open more information;
 * buttons act. Every panel link carries the open-in-panel icon so the two
 * are told apart at a glance. The click stops there: a link on a canvas node
 * must not also toggle the node.
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
  return (
    <a
      href={hrefFor(tab)}
      className={[styles.link, className].filter(Boolean).join(' ')}
      aria-label={ariaLabel}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        openTab(tab);
      }}
    >
      {children}
      {icon && <OpenInPanelIcon className={styles.icon} />}
    </a>
  );
}
