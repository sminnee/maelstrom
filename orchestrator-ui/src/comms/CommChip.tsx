import { useCommsById } from '../api/comms';
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

/**
 * The comms a task feeds, at the end of a row: the first as a chip, and `+n` for the rest.
 */
export function CommChips({ ids }: { ids: readonly string[] }) {
  const byId = useCommsById();
  const [first, ...rest] = ids;
  if (first === undefined) return null;
  const comm = byId.get(first) ?? { id: first, codename: '', title: '' };
  const others = rest.map((id) => byId.get(id)?.codename || id).join(', ');
  return (
    <span className={styles.comms}>
      <CommChip comm={comm} />
      {rest.length > 0 && (
        <span className={styles.more} title={others}>
          +{rest.length}
        </span>
      )}
    </span>
  );
}
