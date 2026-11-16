import type { Comm } from '../protocol/entities';
import { useCommDetail } from '../nav/useOverlays';
import { commLabel } from '../selectors/comms';
import styles from './CommChip.module.css';

/**
 * A comm as a pill in the one comm colour, `--comm`: its **Comm codename**, or its id before it
 * has one. The title names the comm in full. A click opens the comm editor, over any view.
 */
export function CommChip({
  comm,
  className,
}: {
  comm: Pick<Comm, 'id' | 'codename' | 'title'>;
  className?: string;
}) {
  const detail = useCommDetail();
  const label = commLabel(comm);
  return (
    <button
      type="button"
      className={[styles.chip, 'nodrag nopan', className].filter(Boolean).join(' ')}
      data-comm-id={comm.id}
      title={comm.title ? `${label}: ${comm.title}` : label}
      onClick={(e) => {
        // The node behind it is itself a click target: opening the comm must not expand it.
        e.stopPropagation();
        detail.open(comm.id);
      }}
    >
      <span className="truncate">{label}</span>
    </button>
  );
}
