import type { ReactNode } from 'react';
import { ExternalLinkIcon } from './ExternalLinkIcon';
import styles from './ExternalLink.module.css';

/**
 * A link that leaves the app — a GitHub PR, a worktree's dev env.
 *
 * The sibling of `PanelLink`. It stops the click for the same reason, but
 * never calls `preventDefault`: the browser's own navigation is the behaviour.
 */
export function ExternalLink({
  href,
  children,
  className,
  'aria-label': ariaLabel,
}: {
  href: string;
  children: ReactNode;
  className?: string;
  'aria-label'?: string;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={[styles.link, className].filter(Boolean).join(' ')}
      aria-label={ariaLabel}
      onClick={(e) => e.stopPropagation()}
    >
      {children}
      <ExternalLinkIcon className={styles.icon} />
    </a>
  );
}
