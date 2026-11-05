import { useEffect, useId, useState } from 'react';
import { useAddProject } from '../api/projects';
import { useWorld } from '../api/useWorld';
import type { OperationId } from '../protocol/ids';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { Dialog, DialogFooter, DialogHeader } from '../ui/Dialog';
import fieldStyles from '../ui/Dialog.module.css';
import { Spinner } from '../ui/Spinner';
import styles from './AddProject.module.css';

/**
 * Clone a git URL as a new project. The dialog follows the clone to its end: it closes when the
 * project is added, and shows the reason when the server refuses the URL or the clone fails.
 */
export function AddProject({ onClose }: { onClose: () => void }) {
  const add = useAddProject();
  const { world } = useWorld();
  const [gitUrl, setGitUrl] = useState('');
  const [operationId, setOperationId] = useState<OperationId | null>(null);
  const urlId = useId();
  const operation = operationId ? world.operations?.[operationId] : undefined;
  // The operation list may not hold the new id yet: until it does, the clone is still running.
  const ended = operation !== undefined && operation.state !== 'running';
  const running = add.isPending || (operationId !== null && !ended);
  const failure =
    add.error?.message ?? (ended && operation.state !== 'done' ? operation.words : null);

  useEffect(() => {
    if (operation?.state === 'done') onClose();
  }, [operation?.state, onClose]);

  const submit = async () => {
    setOperationId(null);
    const { operationId: id } = await add.mutateAsync({ gitUrl: gitUrl.trim() });
    setOperationId(id);
  };

  return (
    <Dialog label="Add project" onClose={onClose} testId="add-project">
      <DialogHeader title="Add project" onClose={onClose} />
      <div className={fieldStyles.field}>
        <label htmlFor={urlId}>Git URL</label>
        <input
          id={urlId}
          value={gitUrl}
          placeholder="https://github.com/org/repo.git"
          spellCheck={false}
          autoComplete="off"
          disabled={running}
          onChange={(e) => setGitUrl(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== 'Enter' || !gitUrl.trim() || running) return;
            e.preventDefault();
            void submit().catch(() => {});
          }}
        />
      </div>
      {running && operation && (
        <p className={styles.status} aria-live="polite">
          <Spinner /> {operation.words}
        </p>
      )}
      {failure && !running && (
        <p className={styles.error} role="alert">
          {failure}
        </p>
      )}
      <DialogFooter
        aside={
          // A running clone goes on without the dialog: Close, not Cancel.
          <AppButton variant="link" onClick={onClose}>
            {running ? 'Close' : 'Cancel'}
          </AppButton>
        }
      >
        <AppButton
          variant="primary"
          icon={actionIcon('new')}
          disabled={!gitUrl.trim() || running}
          onClick={() => submit().catch(() => {})}
        >
          Add
        </AppButton>
      </DialogFooter>
    </Dialog>
  );
}
