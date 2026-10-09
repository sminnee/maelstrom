import { useSheetDrag } from '../gesture/useSheetDrag';
import { actionIcon } from './actionIcons';
import { Dialog } from './Dialog';
import styles from './SideSheet.module.css';

/**
 * The narrow layout's side sheet: a dialog at the right edge, whose head row
 * starts with a bare × (DESIGN.md, The One Strip Rule). A drag right closes it.
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
  const drag = useSheetDrag(onClose);
  return (
    <Dialog
      label={label}
      placement="side"
      onClose={onClose}
      boxProps={{
        ...drag.handlers,
        style: drag.style,
        'data-dragging': drag.dragging ? '' : undefined,
      }}
    >
      <div className={styles.head}>
        <button
          type="button"
          className={`bareButton ${styles.close}`}
          aria-label="Close"
          onClick={onClose}
        >
          {actionIcon('close')}
        </button>
        {head ?? <h2 className={styles.title}>{label}</h2>}
      </div>
      {children}
    </Dialog>
  );
}
