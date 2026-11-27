import { Fragment, useId, useState, type ReactNode } from 'react';
import type { TaskEdit } from '../api/types';
import { useWorld } from '../api/useWorld';
import { isTerminal, TASK_STATUSES, type TaskStatus } from '../protocol/entities';
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
import { MultiComboBox } from '../ui/MultiComboBox';
import { TextArea } from '../ui/TextArea';
import { PlanningLevelField } from './PlanningLevelField';

/** From `task.PRIORITIES`, highest first. */
const PRIORITIES = ['critical', 'high', 'medium', 'low'];

/** A new task's priority, as the notebook writes it. */
const DEFAULT_PRIORITY = 'medium';

/** Every editable field of a task, as a form holds them. */
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
      <label className={styles.field}>
        <span>Status</span>
        {/* `readOnly` is not a thing on a select, so a locked one is disabled. */}
        <select
          value={draft.status}
          disabled={readOnly}
          onChange={(e) => onChange({ status: e.target.value as TaskStatus })}
        >
          {TASK_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
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
  // Document-global, so two field sets on one page must not share it.
  const contentId = useId();
  const [attached, setAttached] = useState<Attachment[]>([]);

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
          <TextArea
            grow
            id={contentId}
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
 * The fields folded into Advanced: command, mode, priority, model, follows and, for New work,
 * followed by.
 *
 * New work renders this below its planning-level radios, which read the same command and mode
 * two ways, so editing either here re-derives the level. New work passes no `taskId`, so no
 * task is left out of the follows options. Only New work passes `followedBy`.
 *
 * A field that is not a new task's default shows while Advanced is folded: see "Advanced,
 * folded" in `docs/dev/orchestrator-ui.md`.
 */
export function TaskAdvancedFields({
  draft,
  onChange,
  project,
  taskId,
  readOnly,
  followedBy,
}: {
  draft: TaskDraft;
  onChange: (patch: Partial<TaskDraft>) => void;
  project?: string;
  taskId?: TaskId;
  readOnly?: boolean;
  /** The tasks that will follow the new task. Only New work passes it. */
  followedBy?: { value: TaskId[]; onChange: (followedBy: TaskId[]) => void };
}) {
  const [open, setOpen] = useState(false);
  // `row`: the field shares one row with the other `row` fields while Advanced is open.
  const fields: { key: string; showsFolded: boolean; row?: true; node: ReactNode }[] = [
    {
      key: 'command',
      showsFolded: false,
      node: (
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
      ),
    },
    {
      key: 'mode',
      showsFolded: false,
      node: (
        <label className={styles.field}>
          <span>Mode</span>
          <ModeSelect
            mode={draft.mode}
            onChange={(mode) => onChange({ mode })}
            readOnly={readOnly}
          />
        </label>
      ),
    },
    {
      key: 'priority',
      showsFolded: draft.priority !== DEFAULT_PRIORITY,
      node: (
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
      ),
    },
    {
      key: 'model',
      showsFolded: draft.model !== UNSET_MODEL,
      row: true,
      node: (
        <label className={styles.field}>
          <span>Model</span>
          <ModelSelect
            model={draft.model}
            onChange={(model) => onChange({ model })}
            readOnly={readOnly}
          />
        </label>
      ),
    },
    {
      key: 'executeModel',
      showsFolded: draft.executeModel !== UNSET_MODEL,
      row: true,
      node: (
        <label className={styles.field}>
          <span>Execute Model</span>
          <ExecuteModelSelect
            model={draft.executeModel}
            onChange={(executeModel) => onChange({ executeModel })}
            readOnly={readOnly}
          />
        </label>
      ),
    },
    {
      key: 'follows',
      showsFolded: draft.follows.length > 0,
      node: (
        <TaskFollowsField
          follows={draft.follows}
          onChange={(follows) => onChange({ follows })}
          project={project ?? ''}
          taskId={taskId}
          exclude={followedBy?.value}
          readOnly={readOnly}
        />
      ),
    },
    ...(followedBy
      ? [
          {
            key: 'followedBy',
            showsFolded: followedBy.value.length > 0,
            node: (
              <TaskFollowsField
                label="Followed by"
                follows={followedBy.value}
                onChange={followedBy.onChange}
                project={project ?? ''}
                exclude={draft.follows}
                readOnly={readOnly}
              />
            ),
          },
        ]
      : []),
  ];
  const plain = (f: (typeof fields)[number]) => <Fragment key={f.key}>{f.node}</Fragment>;
  const rowed = fields.filter((f) => f.row);
  return (
    <>
      <details
        className={styles.advanced}
        open={open}
        onToggle={(e) => setOpen(e.currentTarget.open)}
      >
        <summary>Advanced</summary>
        {open &&
          fields.map((f) =>
            !f.row ? (
              plain(f)
            ) : f === rowed[0] ? (
              <div key="row" className={styles.row}>
                {rowed.map(plain)}
              </div>
            ) : null,
          )}
      </details>
      {!open && fields.filter((f) => f.showsFolded).map(plain)}
    </>
  );
}

/**
 * Every task a task list may name, as a multi-select: the tasks of its own project first, then
 * the open tasks of every other project, by project. A task already named stays listed, so its
 * chip keeps its label. Excludes `taskId`, when given, mirroring `canConnect`'s no-self-edge
 * rule (see `canvas/connect.ts`), and each id in `exclude`: a task in both of New work's lists
 * would make a cycle.
 *
 * Follows and New work's Followed by both use it. It uses the same write semantics as the
 * canvas wire, `followsAfterConnect` / `followsAfterDisconnect`, so picking an option here and
 * dragging a wire there agree on what the write replaces.
 */
export function TaskFollowsField({
  follows,
  onChange,
  project,
  taskId,
  exclude = [],
  readOnly,
  label = 'Follows',
}: {
  follows: TaskId[];
  onChange: (follows: TaskId[]) => void;
  project: string;
  /** The task being edited, left out of the options. New work has none. */
  taskId?: TaskId;
  /** Ids the other list of New work names. */
  exclude?: TaskId[];
  readOnly?: boolean;
  label?: string;
}) {
  // Explicit id, not a wrapping label: the chips sit between the label and
  // the field, the way `AttachField` does above, so an implicit association
  // would fold their text into the field's accessible name.
  const followsId = useId();
  const { world } = useWorld();
  const all = Object.values(world.tasks).filter((t) => t.id !== taskId && !exclude.includes(t.id));
  const siblings = all.filter((t) => t.project === project);
  const elsewhere = all
    .filter((t) => t.project !== project && (!isTerminal(t) || follows.includes(t.id)))
    .sort((a, b) => a.project.localeCompare(b.project));
  const options: ComboOption[] = [...siblings, ...elsewhere].map((t) => ({
    value: t.id,
    label: t.title,
  }));

  // Diffed against the current list and routed through the same
  // connect/disconnect helpers the canvas wire uses, rather than writing
  // `next` straight through — see the docstring above for why the two paths
  // must agree on what a change means.
  const change = (next: string[]) => {
    let result = follows;
    for (const id of follows) if (!next.includes(id)) result = followsAfterDisconnect(result, id);
    for (const id of next) if (!follows.includes(id)) result = followsAfterConnect(result, id);
    onChange(result);
  };

  return (
    <div className={styles.field}>
      <label htmlFor={followsId}>{label}</label>
      <MultiComboBox
        id={followsId}
        value={follows}
        options={options}
        onChange={change}
        readOnly={readOnly}
      />
    </div>
  );
}

/** The permission mode a session launches under. */
export function ModeSelect({
  mode,
  onChange,
  readOnly,
  modes = MODES,
}: {
  mode: PermissionMode;
  onChange: (mode: PermissionMode) => void;
  readOnly?: boolean;
  /** The modes on offer, for work that cannot run under all of them. */
  modes?: readonly PermissionMode[];
}) {
  return (
    <select
      value={mode}
      disabled={readOnly}
      onChange={(e) => onChange(e.target.value as PermissionMode)}
    >
      {modes.map((m) => (
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
