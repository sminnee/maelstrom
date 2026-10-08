import { useCallback, useId, useMemo, useState } from 'react';
import { useComm, useCreateComm, useUpdateComm, type CommEdit } from '../api/comms';
import { useUpdateTask } from '../api/tasks';
import type { TaskRow } from '../api/types';
import { useWorld } from '../api/useWorld';
import type { Comm } from '../protocol/entities';
import { landingStrip } from '../selectors/comms';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { ComboBox } from '../ui/ComboBox';
import { Dialog, DialogFooter, DialogHeader } from '../ui/Dialog';
import fieldStyles from '../ui/Dialog.module.css';
import { TextArea } from '../ui/TextArea';
import styles from './CommEditor.module.css';

interface Draft {
  title: string;
  content: string;
  recipients: string[];
}

const EMPTY: Draft = { title: '', content: '', recipients: [] };

/**
 * Edits one comm, or writes a new one when `commId` is `null`. A new comm
 * links to no task, so once it is written the editor opens on it, where the
 * links are.
 */
export function CommEditor({
  commId,
  onCreated,
  onClose,
}: {
  commId: string | null;
  onCreated: (id: string) => void;
  onClose: () => void;
}) {
  const comm = useComm(commId);
  if (commId === null) return <CommForm comm={null} onCreated={onCreated} onClose={onClose} />;
  if (comm.data) return <CommForm comm={comm.data} onCreated={onCreated} onClose={onClose} />;
  return (
    <Dialog label={commId} onClose={onClose} testId="comm-editor-wait">
      <p role={comm.isError ? 'alert' : undefined}>
        {comm.isError ? `Could not load ${commId}: ${comm.error.message}` : 'Loading…'}
      </p>
      <DialogFooter
        aside={
          <AppButton variant="link" onClick={onClose}>
            Cancel
          </AppButton>
        }
      />
    </Dialog>
  );
}

function CommForm({
  comm,
  onCreated,
  onClose,
}: {
  comm: Comm | null;
  onCreated: (id: string) => void;
  onClose: () => void;
}) {
  const create = useCreateComm();
  const update = useUpdateComm();
  // Frozen, as the task editor's is: the comm moves as the server publishes,
  // and a diff against a moved copy would send a field the user never touched.
  const [opened] = useState<Draft>(() =>
    comm ? { title: comm.title, content: comm.content, recipients: comm.recipients } : EMPTY,
  );
  const [draft, setDraft] = useState(opened);
  // What the recipient field holds but has not added yet. Save adds it too.
  const [recipient, setRecipient] = useState('');
  const [confirming, setConfirming] = useState(false);
  const titleId = useId();
  const contentId = useId();
  const recipientId = useId();

  const withPending = (d: Draft): Draft => {
    const extra = recipient.trim();
    return extra && !d.recipients.includes(extra)
      ? { ...d, recipients: [...d.recipients, extra] }
      : d;
  };
  const dirty = Object.keys(changed(opened, withPending(draft))).length > 0;

  const leave = useCallback(() => {
    if (dirty) setConfirming(true);
    else onClose();
  }, [dirty, onClose]);

  const addRecipient = () => {
    setDraft(withPending);
    setRecipient('');
  };

  const save = async () => {
    const final = withPending(draft);
    if (!comm) {
      const { id } = await create.mutateAsync({
        title: final.title,
        content: final.content,
        recipients: final.recipients,
      });
      onCreated(id);
      return;
    }
    const fields = changed(opened, final);
    if (Object.keys(fields).length > 0) {
      await update.mutateAsync({ commId: comm.id, fields });
    }
    onClose();
  };

  return (
    <Dialog label={comm ? comm.title : 'New comm'} onClose={leave}>
      <DialogHeader title={comm ? comm.id : 'New comm'} onClose={leave} />
      <label className={fieldStyles.field} htmlFor={titleId}>
        <span>Title</span>
        <input
          id={titleId}
          value={draft.title}
          onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))}
        />
      </label>
      <div className={fieldStyles.field}>
        <label htmlFor={contentId}>Content</label>
        <TextArea
          grow
          id={contentId}
          rows={2}
          value={draft.content}
          onChange={(e) => setDraft((d) => ({ ...d, content: e.target.value }))}
        />
      </div>
      <div className={fieldStyles.field}>
        <label htmlFor={recipientId}>Recipients</label>
        {draft.recipients.length > 0 && (
          <ul className={styles.chips} aria-label="Recipients">
            {draft.recipients.map((r) => (
              <li key={r} className={styles.chip}>
                {r}
                <button
                  type="button"
                  className={styles.remove}
                  aria-label={`Remove ${r}`}
                  onClick={() =>
                    setDraft((d) => ({ ...d, recipients: d.recipients.filter((x) => x !== r) }))
                  }
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
        <input
          id={recipientId}
          value={recipient}
          placeholder="A channel, an address or a note; Enter adds it"
          onChange={(e) => setRecipient(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== 'Enter') return;
            e.preventDefault();
            addRecipient();
          }}
        />
      </div>
      {comm && <LinkedTasks comm={comm} />}
      {confirming && (
        <p className={styles.confirm} role="alert">
          <span>Throw away your changes?</span>
          <AppButton variant="link" onClick={() => setConfirming(false)}>
            Keep editing
          </AppButton>
          <AppButton onClick={onClose}>Discard</AppButton>
        </p>
      )}
      <DialogFooter
        aside={
          <AppButton variant="link" onClick={leave}>
            Cancel
          </AppButton>
        }
      >
        {comm && <OpenCloseButton comm={comm} dirty={dirty} onClosed={onClose} />}
        <AppButton
          variant="primary"
          icon={actionIcon(comm ? 'save' : 'new')}
          disabled={!draft.title.trim() || (comm !== null && !dirty)}
          onClick={save}
        >
          {comm ? 'Save' : 'Create'}
        </AppButton>
      </DialogFooter>
    </Dialog>
  );
}

/**
 * Close a comm once everyone is told; it leaves the open list. Reopen brings it back. Closing
 * also closes the editor, so it waits while the form holds unsaved edits.
 */
function OpenCloseButton({
  comm,
  dirty,
  onClosed,
}: {
  comm: Comm;
  dirty: boolean;
  onClosed: () => void;
}) {
  const update = useUpdateComm();
  if (comm.closedAt) {
    return (
      <AppButton
        icon={actionIcon('reopen')}
        onClick={() => update.mutateAsync({ commId: comm.id, fields: { closed: false } })}
      >
        Reopen comm
      </AppButton>
    );
  }
  return (
    <AppButton
      icon={actionIcon('resolve')}
      disabled={dirty}
      title={dirty ? 'Save or cancel your changes first' : undefined}
      onClick={async () => {
        await update.mutateAsync({ commId: comm.id, fields: { closed: true } });
        onClosed();
      }}
    >
      Close comm
    </AppButton>
  );
}

/**
 * The tasks whose work the comm reports, each with how far it has landed, and
 * the picker that links another. A link is the task's own `comms` field, so
 * both controls write the task.
 */
function LinkedTasks({ comm }: { comm: Comm }) {
  const { world } = useWorld();
  const updateTask = useUpdateTask();
  const [picked, setPicked] = useState('');
  const pickerId = useId();
  const linked = comm.taskIds
    .map((id) => world.tasks[id])
    .filter((t): t is TaskRow => t !== undefined);
  const options = useMemo(
    () =>
      Object.values(world.tasks)
        .filter((t) => !t.comms.includes(comm.id) && t.status !== 'cancelled')
        .sort((a, b) => a.id.localeCompare(b.id))
        .map((t) => ({ value: t.id, label: t.title })),
    [world.tasks, comm.id],
  );
  const target = options.some((o) => o.value === picked.trim())
    ? world.tasks[picked.trim()]
    : undefined;

  const setComms = (task: TaskRow, comms: string[]) =>
    updateTask.mutateAsync({ taskId: task.id, fields: { comms } });

  return (
    <section className={styles.tasks} aria-label="Linked tasks">
      <h3 className={styles.heading}>Tasks</h3>
      {linked.length === 0 ? (
        <p className={styles.none}>No task is linked yet.</p>
      ) : (
        <ul className={styles.taskList}>
          {linked.map((task) => (
            <li key={task.id} className={styles.task} data-task-id={task.id}>
              <span className={styles.taskId}>{task.id}</span>
              <span className={styles.taskTitle}>{task.title}</span>
              <LandingStrip task={task} />
              <AppButton
                variant="quiet"
                icon={actionIcon('unlink')}
                aria-label={`Unlink ${task.id}`}
                onClick={() =>
                  setComms(
                    task,
                    task.comms.filter((id) => id !== comm.id),
                  )
                }
              >
                Unlink
              </AppButton>
            </li>
          ))}
        </ul>
      )}
      <div className={fieldStyles.field}>
        <label htmlFor={pickerId}>Link a task</label>
        <ComboBox
          id={pickerId}
          value={picked}
          options={options}
          onChange={setPicked}
          placeholder="A task id or title"
        />
      </div>
      <div className={styles.linkRow}>
        <AppButton
          icon={actionIcon('link')}
          disabled={!target}
          onClick={async () => {
            if (!target) return;
            await setComms(target, [...target.comms, comm.id]);
            setPicked('');
          }}
        >
          Link
        </AppButton>
      </div>
    </section>
  );
}

/** `merged · UAT ✓ · live ○`: the step reached, then each deploy env's mark. */
function LandingStrip({ task }: { task: TaskRow }) {
  const parts = landingStrip(task);
  return (
    <span className={styles.strip} data-testid="landing-strip">
      {parts.map((part, i) => (
        <span key={part.label} title={part.words} data-mark={part.mark || undefined}>
          {i > 0 && ' · '}
          {part.label}
          {part.mark && ` ${part.mark}`}
        </span>
      ))}
    </span>
  );
}

/** Only what the user moved. */
function changed(before: Draft, after: Draft): CommEdit {
  const fields: CommEdit = {};
  if (after.title !== before.title) fields.title = after.title;
  if (after.content !== before.content) fields.content = after.content;
  if (after.recipients.join('\n') !== before.recipients.join('\n')) {
    fields.recipients = after.recipients;
  }
  return fields;
}
