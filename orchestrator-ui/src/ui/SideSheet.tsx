import { actionIcon } from './actionIcons';
import { Dialog } from './Dialog';
import styles from './SideSheet.module.css';

/**
 * The narrow layout's side sheet: a dialog at the right edge, whose head row
 * starts with a bare × (DESIGN.md, The One Strip Rule).
 */
export function SideSheet({
  label,
  onClose,
  head,
  children,
}: {
  /** The sheet's accessible name. */
  label: string;
  onClose: () => void;
  /** The head row's content, after the ×. Without it the head shows `label`. */
  head?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Dialog label={label} placement="side" onClose={onClose}>
      <div className={styles.head}>
        <button type="button" className={styles.close} aria-label="Close" onClick={onClose}>
          {actionIcon('close')}
        </button>
        {head ?? <h2 className={styles.title}>{label}</h2>}
      </div>
      {children}
    </Dialog>
  );
}
