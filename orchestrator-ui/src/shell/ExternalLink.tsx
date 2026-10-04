import type { ReactNode } from 'react';
import { actionIcon, type ActionVerb } from '../ui/actionIcons';
import styles from './link.module.css';

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
  icon = 'external',
  newTab = true,
  'aria-label': ariaLabel,
}: {
  href: string;
  children: ReactNode;
  className?: string;
  icon?: ActionVerb;
  /** Off for a link another app takes, such as `cmux:`: a new tab would stay blank. */
  newTab?: boolean;
  'aria-label'?: string;
}) {
  return (
    <a
      href={href}
      target={newTab ? '_blank' : undefined}
      rel={newTab ? 'noopener noreferrer' : undefined}
      className={[styles.link, className].filter(Boolean).join(' ')}
      aria-label={ariaLabel}
      onClick={(e) => e.stopPropagation()}
    >
      {children}
      {actionIcon(icon, styles.icon)}
    </a>
  );
}
