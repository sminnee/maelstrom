import { useCommsById } from '../api/comms';
import type { Comm } from '../protocol/entities';
import { useCommDetail } from '../nav/useOverlays';
import { commLabel } from '../selectors/comms';
import styles from './CommChip.module.css';

/**
 * A comm as a pill in the one comm colour, `--comm`: its **Comm codename**, or its id before it
 * has one. The title names the comm in full. A click opens the comm editor, over any view.
 * With `interactive={false}` it is a plain span: a chip inside a link must not be a button.
 */
export function CommChip({
  comm,
  className,
  interactive = true,
}: {
  comm: Pick<Comm, 'id' | 'codename' | 'title'>;
  className?: string;
  interactive?: boolean;
}) {
  const detail = useCommDetail();
  const label = commLabel(comm);
  const shared = {
    'data-comm-id': comm.id,
    title: comm.title ? `${label}: ${comm.title}` : label,
    children: <span className="truncate">{label}</span>,
  };
  if (!interactive)
    return (
      <span
        className={[styles.chip, styles.static, className].filter(Boolean).join(' ')}
        {...shared}
      />
    );
  return (
    <button
      type="button"
      className={[styles.chip, 'nodrag nopan', className].filter(Boolean).join(' ')}
      {...shared}
      onClick={(e) => {
        // The node behind it is itself a click target: opening the comm must not expand it.
        e.stopPropagation();
        detail.open(comm.id);
      }}
    />
  );
}

/**
 * The comms a task feeds, at the end of a row: the first as a chip, and `+n` for the rest.
 */
export function CommChips({
  ids,
  interactive = true,
}: {
  ids: readonly string[];
  interactive?: boolean;
}) {
  const byId = useCommsById();
  const [first, ...rest] = ids;
  if (first === undefined) return null;
  const comm = byId.get(first) ?? { id: first, codename: '', title: '' };
  const others = rest.map((id) => byId.get(id)?.codename || id).join(', ');
  return (
    <span className={styles.comms}>
      <CommChip comm={comm} interactive={interactive} />
      {rest.length > 0 && (
        <span className={styles.more} title={others}>
          +{rest.length}
        </span>
      )}
    </span>
  );
}
