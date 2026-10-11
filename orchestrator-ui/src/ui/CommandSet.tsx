import type { ReactNode } from 'react';
import styles from './CommandSet.module.css';

/**
 * Commands that one switch disables: a `<fieldset>` reaches every button in
 * it natively, and links stay live. A unit's commands sit in one while an
 * operation runs on it.
 */
export function CommandSet({ disabled, children }: { disabled: boolean; children: ReactNode }) {
  return (
    <fieldset disabled={disabled} className={styles.set}>
      {children}
    </fieldset>
  );
}
