import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { TaskEdit } from '../api/types';
import { useWorld } from '../api/useWorld';
import type { TaskId } from '../protocol/ids';
import type { PermissionMode } from '../protocol/modes';
import { MODES } from '../protocol/modes';
import { UNSET_MODEL, MODELS, EXECUTE_MODELS } from '../protocol/models';
import { KNOWN_COMMANDS } from '../protocol/phase';
import { withoutRef, type Attachment } from '../api/attachments';
import { followsAfterConnect, followsAfterDisconnect } from '../canvas/connect';
import { AttachField } from '../ui/AttachField';
import { ComboBox, type ComboOption } from '../ui/ComboBox';
import styles from '../ui/Dialog.module.css';
import { PlanningLevelField } from './PlanningLevelField';

/** From `task.PRIORITIES`, highest first. */
const PRIORITIES = ['critical', 'high', 'medium', 'low'];

/**
 * Every editable field of a task, as a form holds them. New work has no
 * existing task to follow anything, so it seeds `follows` to `[]` and never
 * renders the control that would patch it — inert there, live in the editor.
 */
export type TaskDraft = Required<TaskEdit>;

/** The known commands, plus the empty one that means "run the task itself". */
const COMMAND_OPTIONS: readonly ComboOption[] = [
  // A bare `{ value: '' }` renders as a blank row with no accessible name, so
  // the empty command says what choosing it means.
  { value: '', label: 'Run the task itself' },
  ...KNOWN_COMMANDS.map((value) => ({ value })),
];

/**
 * A task's fields: Title, Content and Branch, with the rest folded into
 * Advanced. Both surfaces that write a task render this same component — the
 * editor over an existing task, the new-work form over an inferred one — so
 * the two cannot drift apart on what a task's fields are or how they read.
 *
 * The three parts are exported separately as well, because new work interleaves
 * its own controls between them: a Suggest button beside Branch, and the
 * planning-level radios above Advanced. Composing the parts is what keeps one
 * definition of a task's fields for both surfaces — see
 * `docs/dev/orchestrator-ui.md`, "Starting new work".
 */
export function TaskFields({
  draft,
  onChange,
  project,
  taskId,
  bucket,
  readOnly,
}: {
  draft: TaskDraft;
  onChange: (patch: Partial<TaskDraft>) => void;
  project: string;
  /** The task being edited, excluded from its own follows list. */
  taskId: TaskId;
  /** Groups this task's images in the task repo. */
  bucket: string;
  /** Shows the task without offering to change it. */
  readOnly?: boolean;
}) {
  return (
    <>
      <TaskTitleField draft={draft} onChange={onChange} readOnly={readOnly} />
      <TaskContentField
        draft={draft}
        onChange={onChange}
        project={project}
        bucket={bucket}
        readOnly={readOnly}
      />
      <label className={styles.field}>
        <span>Branch</span>
        <input
          value={draft.branch}
          readOnly={readOnly}
          onChange={(e) => onChange({ branch: e.target.value })}
        />
      </label>
      <PlanningLevelField
        command={draft.command}
        mode={draft.mode}
        onChange={(fields) => onChange(fields)}
        readOnly={readOnly}
      />
      <TaskAdvancedFields
        draft={draft}
        onChange={onChange}
        project={project}
        taskId={taskId}
        readOnly={readOnly}
      />
    </>
  );
}

/**
 * The task's title.
 *
 * Its own part, because new work shows a title without a content field: its
 * prose field is the content, so rendering both would ask for the same text
 * twice.
 */
export function TaskTitleField({
  draft,
  onChange,
  readOnly,
}: {
  draft: TaskDraft;
  onChange: (patch: Partial<TaskDraft>) => void;
  readOnly?: boolean;
}) {
  return (
    <label className={styles.field}>
      <span>Title</span>
      <input
        value={draft.title}
        readOnly={readOnly}
        onChange={(e) => onChange({ title: e.target.value })}
      />
    </label>
  );
}

/** The task's content, with its own attach control. */
export function TaskContentField({
  draft,
  onChange,
  project,
  bucket,
  readOnly,
}: {
  draft: TaskDraft;
  onChange: (patch: Partial<TaskDraft>) => void;
  project: string;
  /** Groups this task's images in the task repo. */
  bucket: string;
  readOnly?: boolean;
}) {
  // The content field shows the whole task body: it grows to fit, and the
  // dialog scrolls.
  // Document-global, so two field sets on one page must not share it.
  const contentId = useId();
  const content = useRef<HTMLTextAreaElement>(null);
  const [attached, setAttached] = useState<Attachment[]>([]);
  const grow = useCallback((el: HTMLTextAreaElement | null) => {
    content.current = el;
    fitToText(el);
  }, []);
  useEffect(() => fitToText(content.current), [draft.content]);

  return (
    <>
      {/* An explicit id, not a wrapping label: AttachField sits between the
          label and the field, so the implicit association is broken. */}
      <div className={styles.field}>
        <label htmlFor={contentId}>Content</label>
        <AttachField
          project={project}
          bucket={bucket}
          attached={attached}
          onAttach={(a) => {
            setAttached((was) => [...was, a]);
            // Appended to the content, not held beside it: the content is what
            // the task stores and what `build_prompt` sends, so a ref outside
            // it would never reach the agent. It also keeps `changed()`'s
            // shallow diff comparing strings.
            onChange({
              content: draft.content ? `${draft.content}\n\n${a.markdown}` : a.markdown,
            });
          }}
          onRemove={(image) => {
            setAttached((was) => was.filter((w) => w.url !== image.url));
            onChange({ content: withoutRef(draft.content, image) });
          }}
        >
          <textarea
            id={contentId}
            ref={grow}
            rows={1}
            value={draft.content}
            readOnly={readOnly}
            onChange={(e) => onChange({ content: e.target.value })}
          />
        </AttachField>
      </div>
    </>
  );
}

/**
 * The fields folded into Advanced: command, mode, priority, model and follows.
 *
 * New work renders this below its planning-level radios, which read the same
 * command and mode two ways — so editing either here re-derives the level.
 * New work has no existing task, so it passes no `taskId` and the follows
 * control does not render.
 */
export function TaskAdvancedFields({
  draft,
  onChange,
  project,
  taskId,
  readOnly,
}: {
  draft: TaskDraft;
  onChange: (patch: Partial<TaskDraft>) => void;
  project?: string;
  taskId?: TaskId;
  readOnly?: boolean;
}) {
  return (
    <details className={styles.advanced}>
      <summary>Advanced</summary>
      <label className={styles.field}>
        <span>Command</span>
        {/* Free-form in the notebook, so this offers the known ones and
            keeps anything else typed. Empty runs the task itself. */}
        <ComboBox
          value={draft.command}
          options={COMMAND_OPTIONS}
          readOnly={readOnly}
          onChange={(command) => onChange({ command })}
        />
      </label>
      <label className={styles.field}>
        <span>Mode</span>
        <ModeSelect mode={draft.mode} onChange={(mode) => onChange({ mode })} readOnly={readOnly} />
      </label>
      <label className={styles.field}>
        <span>Priority</span>
        {/* `readOnly` is not a thing on a select, so a locked one is disabled. */}
        <select
          value={draft.priority}
          disabled={readOnly}
          onChange={(e) => onChange({ priority: e.target.value })}
        >
          {PRIORITIES.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </label>
      <div className={styles.row}>
        <label className={styles.field}>
          <span>Model</span>
          <ModelSelect
            model={draft.model}
            onChange={(model) => onChange({ model })}
            readOnly={readOnly}
          />
        </label>
        <label className={styles.field}>
          <span>Execute Model</span>
          <ExecuteModelSelect
            model={draft.executeModel}
            onChange={(executeModel) => onChange({ executeModel })}
            readOnly={readOnly}
          />
        </label>
      </div>
      {/* New work passes no `taskId`. */}
      {taskId && (
        <TaskFollowsField
          follows={draft.follows}
          onChange={(follows) => onChange({ follows })}
          project={project ?? ''}
          taskId={taskId}
          readOnly={readOnly}
        />
      )}
    </details>
  );
}

/**
 * Every sibling task in the same project the task may follow, as a
 * multi-select. Excludes the task's own id, mirroring `canConnect`'s
 * no-self-edge rule (see `canvas/connect.ts`).
 *
 * Uses the same write semantics as the canvas wire — `followsAfterConnect` /
 * `followsAfterDisconnect` — so checking a box here and dragging a wire there
 * agree on what the write replaces.
 */
export function TaskFollowsField({
  follows,
  onChange,
  project,
  taskId,
  readOnly,
}: {
  follows: TaskId[];
  onChange: (follows: TaskId[]) => void;
  project: string;
  taskId: TaskId;
  readOnly?: boolean;
}) {
  const { world } = useWorld();
  const siblings = Object.values(world.tasks).filter(
    (t) => t.project === project && t.id !== taskId,
  );
  return (
    <fieldset className={styles.field} disabled={readOnly}>
      <legend>Follows</legend>
      {siblings.map((t) => (
        <label key={t.id} className={styles.field}>
          <input
            type="checkbox"
            checked={follows.includes(t.id)}
            onChange={() =>
              onChange(
                follows.includes(t.id)
                  ? followsAfterDisconnect(follows, t.id)
                  : followsAfterConnect(follows, t.id),
              )
            }
          />
          {t.id} — {t.title}
        </label>
      ))}
    </fieldset>
  );
}

/** The permission mode a session launches under. */
export function ModeSelect({
  mode,
  onChange,
  readOnly,
}: {
  mode: PermissionMode;
  onChange: (mode: PermissionMode) => void;
  readOnly?: boolean;
}) {
  return (
    <select
      value={mode}
      disabled={readOnly}
      onChange={(e) => onChange(e.target.value as PermissionMode)}
    >
      {MODES.map((m) => (
        <option key={m} value={m}>
          {m}
        </option>
      ))}
    </select>
  );
}

/** The model a session runs under. A stored model outside `MODELS` is offered too. */
export function ModelSelect({
  model,
  onChange,
  readOnly,
}: {
  model: string;
  onChange: (model: string) => void;
  readOnly?: boolean;
}) {
  const offered: readonly string[] = [UNSET_MODEL, ...MODELS];
  return (
    <select value={model} disabled={readOnly} onChange={(e) => onChange(e.target.value)}>
      {(offered.includes(model) ? offered : [...offered, model]).map((m) => (
        <option key={m} value={m}>
          {m === UNSET_MODEL ? 'not set' : m}
        </option>
      ))}
    </select>
  );
}

/**
 * The model an execute session switches to on plan approval. Unset means no
 * switch — the session keeps the plan's model. A stored model outside
 * `EXECUTE_MODELS` is offered too.
 */
export function ExecuteModelSelect({
  model,
  onChange,
  readOnly,
}: {
  model: string;
  onChange: (model: string) => void;
  readOnly?: boolean;
}) {
  const offered: readonly string[] = [UNSET_MODEL, ...EXECUTE_MODELS];
  return (
    <select value={model} disabled={readOnly} onChange={(e) => onChange(e.target.value)}>
      {(offered.includes(model) ? offered : [...offered, model]).map((m) => (
        <option key={m} value={m}>
          {m === UNSET_MODEL ? '(Same as plan)' : m}
        </option>
      ))}
    </select>
  );
}

/** Set a textarea's height to the height of its text. */
function fitToText(el: HTMLTextAreaElement | null) {
  if (!el) return;
  // Collapse first, or the height only ever grows: `scrollHeight` includes
  // whatever height is already set.
  el.style.height = 'auto';
  el.style.height = `${el.scrollHeight}px`;
}
