import type { ReactNode } from 'react';
import styles from './TableScroll.module.css';

/**
 * The region a wide table scrolls in, so the table does not widen the prose
 * around it. The wrapper scrolls, not the table: a table set to
 * `display: block` loses its semantics in some screen readers. Focusable, so
 * a keyboard can scroll it.
 *
 * A surface that sets `--table-breakout` lets the table grow past its column,
 * centred on it (DESIGN.md § Measure).
 */
export function TableScroll({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={className ? `${styles.tableScroll} ${className}` : styles.tableScroll}
      role="region"
      aria-label="Table"
      tabIndex={0}
    >
      {children}
    </div>
  );
}
