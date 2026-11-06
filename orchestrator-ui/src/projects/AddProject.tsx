import { useEffect, useId, useRef, useState } from 'react';
import { useAddProject } from '../api/projects';
import { useWorld } from '../api/useWorld';
import { useAddProjectDialog } from '../nav/useOverlays';
import type { OperationId } from '../protocol/ids';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { Dialog, DialogFooter, DialogHeader } from '../ui/Dialog';
import fieldStyles from '../ui/Dialog.module.css';
import { Spinner } from '../ui/Spinner';
import { useSetUpProject } from './useSetUpProject';
import styles from './AddProject.module.css';

/**
 * Clone a git URL as a new project. The location's `add` opens it. The dialog follows the clone
 * to its end: when the project is added, the Project dialog takes its place, and it shows the
 * reason when the server refuses the URL or the clone fails.
 * With "Set up after adding" on, it creates the set-up task before it hands over. A failed create
 * keeps the dialog open with its reason, and Add becomes a retry of the set-up alone.
 * The dialog holds the set-up: closing it during the clone skips the set-up, and says so.
 */
export function AddProject() {
  const { close: onClose, finish } = useAddProjectDialog();
  const add = useAddProject();
  const { world } = useWorld();
  const [gitUrl, setGitUrl] = useState('');
  const [operationId, setOperationId] = useState<OperationId | null>(null);
  const [setUpAfter, setSetUpAfter] = useState(true);
  const setUp = useSetUpProject();
  // The operation whose end was acted on: the world re-renders a done operation many times.
  const finished = useRef<OperationId | null>(null);
  const urlId = useId();
  const operation = operationId ? world.operations?.[operationId] : undefined;
  // The operation list may not hold the new id yet: until it does, the clone is still running.
  const ended = operation !== undefined && operation.state !== 'running';
  const running = add.isPending || (operationId !== null && !ended) || setUp.isPending;
  const failure =
    add.error?.message ??
    setUp.error?.message ??
    (ended && operation.state !== 'done' ? operation.words : null);

  const { setUp: createSetUp } = setUp;
  // The clone is done and only the set-up failed: a second clone would be refused.
  const setUpFailed = operation?.state === 'done' && setUp.isError;
  const retrySetUp = () =>
    createSetUp(operation!.project).then(
      () => finish(operation!.project),
      () => {},
    );
  useEffect(() => {
    if (operation?.state !== 'done' || finished.current === operation.id) return;
    finished.current = operation.id;
    const { project } = operation;
    if (!setUpAfter) return finish(project);
    // The create is awaited: a hand-over first would unmount the dialog that shows its failure.
    createSetUp(project).then(
      () => finish(project),
      () => {},
    );
  }, [operation, setUpAfter, finish, createSetUp]);

  const submit = async () => {
    setOperationId(null);
    setUp.reset();
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
      <label className={styles.check}>
        <input
          type="checkbox"
          checked={setUpAfter}
          disabled={running}
          onChange={(e) => setSetUpAfter(e.target.checked)}
        />
        <span>Set up after adding</span>
      </label>
      {running && operation && (
        <p className={styles.status} aria-live="polite">
          <Spinner /> {operation.words}
          {setUpAfter && operation.state === 'running' && ' Closing now skips the set-up.'}
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
        {setUpFailed ? (
          <AppButton variant="primary" icon={actionIcon('retry')} onClick={retrySetUp}>
            Retry set-up
          </AppButton>
        ) : (
          <AppButton
            variant="primary"
            icon={actionIcon('new')}
            disabled={!gitUrl.trim() || running}
            onClick={() => submit().catch(() => {})}
          >
            Add
          </AppButton>
        )}
      </DialogFooter>
    </Dialog>
  );
}
