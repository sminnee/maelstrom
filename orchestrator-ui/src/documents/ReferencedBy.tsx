import { useId, useMemo, useState } from 'react';
import { useLinkDocument, useUnlinkDocument } from '../api/documents';
import { useWorld } from '../api/useWorld';
import type { DocumentLink } from '../protocol/documents';
import type { DocumentId, TaskId } from '../protocol/ids';
import { linksByFragment } from '../selectors/documents';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { ComboBox } from '../ui/ComboBox';
import fieldStyles from '../ui/Dialog.module.css';
import { useRecent } from '../ui/recent';
import styles from './DocumentTab.module.css';

/**
 * The tasks that link the document, grouped by the place each link names, and
 * the form that links another. The owner links it implicitly, so it is not
 * listed and not offered. The form offers only the server's `fragments`.
 */
export function ReferencedBy({
  documentId,
  ownerTaskId,
  links,
  fragments,
  onShowFragment,
}: {
  documentId: DocumentId;
  ownerTaskId: TaskId;
  links: readonly DocumentLink[];
  fragments: readonly string[];
  onShowFragment: (fragment: string) => void;
}) {
  const { world } = useWorld();
  const link = useLinkDocument();
  const unlink = useUnlinkDocument();
  const recent = useRecent('task');
  const [picked, setPicked] = useState('');
  const [fragment, setFragment] = useState('');
  const pickerId = useId();
  const fragmentId = useId();
  const groups = linksByFragment(links, fragments);
  const options = useMemo(
    () =>
      Object.values(world.tasks)
        .filter((t) => t.id !== ownerTaskId && t.status !== 'cancelled')
        .sort((a, b) => a.id.localeCompare(b.id))
        .map((t) => ({ value: t.id, label: t.title })),
    [world.tasks, ownerTaskId],
  );
  const target = options.some((o) => o.value === picked.trim()) ? picked.trim() : null;

  return (
    <section className={styles.referencedBy} aria-label="Referenced by">
      <h3 className={styles.referencedHeading}>Referenced by</h3>
      {groups.length === 0 && <p className={styles.none}>No other task links this document.</p>}
      {groups.map((group) => (
        <div key={group.fragment} role="group" aria-label={group.fragment || 'Whole document'}>
          {group.fragment ? (
            <AppButton variant="link" onClick={() => onShowFragment(group.fragment)}>
              #{group.fragment}
            </AppButton>
          ) : (
            <span className={styles.fragmentHead}>Whole document</span>
          )}
          <ul className={styles.linkList}>
            {group.taskIds.map((taskId) => {
              const task = world.tasks[taskId];
              return (
                <li key={taskId} className={styles.linkRow} data-task-id={taskId}>
                  <span className={`${styles.linkTask} nowrap`}>{taskId}</span>
                  <span className={`${styles.linkTitle} wrap`}>{task?.title ?? ''}</span>
                  <AppButton
                    variant="quiet"
                    icon={actionIcon('unlink')}
                    aria-label={`Unlink ${taskId}`}
                    onClick={() =>
                      unlink.mutateAsync({ documentId, taskId, fragment: group.fragment })
                    }
                  >
                    Unlink
                  </AppButton>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
      <div className={styles.linkForm}>
        <div className={fieldStyles.field}>
          <label htmlFor={pickerId}>Link a task</label>
          <ComboBox
            id={pickerId}
            value={picked}
            options={options}
            recent={recent.ids}
            onChange={setPicked}
            placeholder="A task id or title"
          />
        </div>
        <div className={fieldStyles.field}>
          <label htmlFor={fragmentId}>To</label>
          <select id={fragmentId} value={fragment} onChange={(e) => setFragment(e.target.value)}>
            <option value="">Whole document</option>
            {fragments.map((f) => (
              <option key={f} value={f}>
                #{f}
              </option>
            ))}
          </select>
        </div>
        <AppButton
          icon={actionIcon('link')}
          disabled={!target || link.isPending}
          onClick={async () => {
            if (!target) return;
            await link.mutateAsync({ documentId, taskId: target, fragment });
            recent.touch(target);
            setPicked('');
          }}
        >
          Link
        </AppButton>
      </div>
      {link.error && (
        <p className={styles.linkError} role="alert">
          {link.error.message}
        </p>
      )}
    </section>
  );
}
