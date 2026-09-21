import { useId, useMemo, useState } from 'react';
import { withoutRef, type Attachment } from '../api/attachments';
import { AttachField } from '../ui/AttachField';
import { useStartAgent } from '../api/agents';
import { ApiError } from '../api/http';
import { useProjects } from '../api/projects';
import { useCreateLinearTask } from '../api/linear';
import { useCreateTask, useInferTask } from '../api/tasks';
import { useWorld } from '../api/useWorld';
import type { PermissionMode } from '../protocol/modes';
import { MODES } from '../protocol/modes';
import { DEFAULT_MODEL, UNSET_MODEL } from '../protocol/models';
import { fieldsForLevel } from '../protocol/planningLevel';
import { projectsInView } from '../selectors/projectsInView';
import type { TaskDraft } from '../tasklist/TaskFields';
import {
  ExecuteModelSelect,
  ModeSelect,
  ModelSelect,
  TaskAdvancedFields,
  TaskTitleField,
} from '../tasklist/TaskFields';
import { useWorktrees } from '../api/worktrees';
import { useAppStore } from '../store/store';
import { AppButton } from '../ui/AppButton';
import { ComboBox } from '../ui/ComboBox';
import { Dialog, DialogFooter, DialogHeader } from '../ui/Dialog';
import { LinearFields } from './LinearFields';
import { PlanningLevelField } from '../tasklist/PlanningLevelField';
import { ProjectField } from './ProjectField';
import { branchFromDraft, titleFromDraft } from '../protocol/branchFromDraft';
import { Spinner } from '../ui/Spinner';
import { retainedKey } from '../ui/retained';
import { useRetained } from '../ui/useRetained';
import dialog from '../ui/Dialog.module.css';
import styles from './NewWork.module.css';

/**
 * What the user is starting: a task in the notebook, an agent tied to none, or
 * a Linear issue to plan.
 *
 * `linear` is offered only for a project that names a Linear team, and is the
 * one kind whose work is defined elsewhere. It is expected to go once the
 * notebook covers the same ground -- see `docs/dev/orchestrator-ui.md`.
 */
type Kind = 'task' | 'agent' | 'linear';

/**
 * Where the planning radios start: `regular`, which runs the task itself under
 * plan mode. The middle of the three, so the agent proposes before it edits
 * without a planning session being asked for.
 */
const DEFAULT_LEVEL_FIELDS = fieldsForLevel('regular');

/**
 * Everything the dialog captures, and the whole of what it holds.
 *
 * One value rather than a key per field, because the prose, its attachments and
 * their bucket only make sense together — see `useRetained`.
 *
 * The task's own fields are held as well: a typed title lost on a close is the
 * same loss the prose case guards against.
 */
interface Captured {
  project: string;
  kind: Kind;
  issue: string;
  draft: string;
  branch: string;
  mode: PermissionMode;
  model: string;
  executeModel: string;
  attached: Attachment[];
  /**
   * Groups this dialog's images in the task repo. Minted once and then held: a
   * re-minted bucket would send the next image to another directory, and would
   * stop `withoutRef` matching the refs already in the prose.
   */
  bucket: string;
  /** The task's own fields, as the one surface now shows them. */
  title: string;
  command: string;
  priority: string;
  taskMode: PermissionMode;
  taskModel: string;
  taskExecuteModel: string;
  taskBase: string;
}

/**
 * A dialog with nothing in it yet.
 *
 * A free agent's own mode and model default here. A task starts at the `regular`
 * planning level and leaves its model unset: `docs/guide/planning.md` asks for
 * that on execute drafts, and Advanced is where one is chosen.
 */
const initialCaptured: Captured = {
  project: '',
  kind: 'task',
  issue: '',
  draft: '',
  branch: '',
  mode: MODES[0],
  model: DEFAULT_MODEL,
  executeModel: UNSET_MODEL,
  attached: [],
  bucket: '',
  title: '',
  command: DEFAULT_LEVEL_FIELDS.command,
  priority: 'medium',
  taskMode: DEFAULT_LEVEL_FIELDS.mode,
  taskModel: UNSET_MODEL,
  taskExecuteModel: UNSET_MODEL,
  taskBase: '',
};

/** A bucket for a dialog that has none held yet. */
const mintBucket = () => `draft-${Math.random().toString(36).slice(2, 10)}`;

/**
 * One form for every kind of new work — see `Kind`.
 *
 * One step: the prose and the task's own fields on the one surface, with a
 * Suggest button that fills the named fields from inference rather than a step
 * that gates on it. See `docs/dev/orchestrator-ui.md`.
 */
export function NewWork() {
  const close = useAppStore((s) => s.setNewWorkOpen);
  const projects = useProjects();
  const worktrees = useWorktrees();
  const infer = useInferTask();
  const create = useCreateTask();
  const start = useStartAgent();
  const plan = useCreateLinearTask();
  // The canvas view, read the way every other view reads it, so the project
  // radios follow the filter bar.
  const { world } = useWorld();
  const filters = useAppStore((s) => s.ui.filters);
  const inView = useMemo(() => projectsInView(world, filters), [world, filters]);

  const names = projects.data?.projects.map((p) => p.name) ?? [];
  // Everything the dialog captures, held as one value -- see `ui/useRetained.ts`.
  // The prose, its attachments and their bucket cannot be held separately: an
  // image's ref lives in the prose, and `withoutRef` matches on a ref that
  // embeds the bucket, so a re-minted bucket would make removing a thumbnail a
  // silent no-op and send the agent a link to an image it never got.
  const [captured, setCaptured, release] = useRetained(retainedKey.newWork(), initialCaptured);
  const { kind, issue, draft, branch, mode, model, executeModel, attached } = captured;
  const { title, command, priority, taskMode, taskModel, taskExecuteModel, taskBase } = captured;
  // One bucket for the dialog's whole life. State with a
  // lazy initialiser, so it is settled once on mount: an expression like
  // `held || mintBucket()` in the render yields a new directory every pass until
  // one is committed, and the server refuses an upload with no bucket at all.
  // `useRetained` reads storage in its own initialiser, so a held bucket is
  // already here to be reused rather than replaced.
  const [bucket] = useState(() => captured.bucket || mintBucket());
  const patch = (fields: Partial<Captured>) => setCaptured((was) => ({ ...was, ...fields }));
  // A held project the world no longer has is dropped rather than carried: the
  // fallback below picks the *first* project, so a stale name would silently
  // write the work against a different one.
  const project = captured.project && names.includes(captured.project) ? captured.project : '';
  // Nothing chosen falls to the one project in view, else the first the world
  // has -- so the radios open on something legal rather than on none.
  const offered = inView.length > 0 ? inView : names;
  const chosen = project || offered[0] || '';
  // Only a project that names a Linear team can plan a Linear issue.
  const hasLinear = projects.data?.projects.find((p) => p.name === chosen)?.hasLinear ?? false;
  // A project change can take the chosen kind off the board, so the form falls
  // back to the one every project has rather than sitting on a dead kind.
  const showing: Kind = kind === 'linear' && !hasLinear ? 'task' : kind;
  const setDraft = (next: string | ((was: string) => string)) =>
    setCaptured((was) => ({
      ...was,
      draft: typeof next === 'function' ? next(was.draft) : next,
    }));

  /**
   * The task as the fields hold it. The prose is the content: it is what the
   * notebook stores and what `build_prompt` sends.
   */
  const task: TaskDraft = {
    title,
    content: draft,
    // New work has no status to choose: a created task always starts in
    // `todo`, and Advanced offers no control that would change it here.
    status: 'todo',
    branch,
    command,
    mode: taskMode,
    priority,
    model: taskModel,
    executeModel: taskExecuteModel,
    base: taskBase,
    follows: [],
  };
  const patchTask = (fields: Partial<TaskDraft>) =>
    setCaptured((was) => ({
      ...was,
      ...(fields.title !== undefined ? { title: fields.title } : {}),
      ...(fields.content !== undefined ? { draft: fields.content } : {}),
      ...(fields.branch !== undefined ? { branch: fields.branch } : {}),
      ...(fields.command !== undefined ? { command: fields.command } : {}),
      ...(fields.mode !== undefined ? { taskMode: fields.mode } : {}),
      ...(fields.priority !== undefined ? { priority: fields.priority } : {}),
      ...(fields.model !== undefined ? { taskModel: fields.model } : {}),
      ...(fields.executeModel !== undefined ? { taskExecuteModel: fields.executeModel } : {}),
      ...(fields.base !== undefined ? { taskBase: fields.base } : {}),
    }));

  // The branches on offer are those with a worktree already open in the
  // chosen project. Anything else typed is kept: a branch with no worktree
  // gets one provisioned on the way.
  const branches = useMemo(
    () =>
      (worktrees.data?.worktrees ?? [])
        .filter((w) => w.project === chosen && !w.isClosed && w.branch)
        .map((w) => w.branch),
    [worktrees.data, chosen],
  );

  // The submits, not inference: Suggest is an `AppButton` and shows its own wait.
  const busy = create.isPending || start.isPending || plan.isPending;
  // The error of the kind that is showing. React Query holds a mutation's
  // error until that same mutation runs again, so a fixed precedence would
  // let a refused start outlive the surface that raised it. A task's own
  // surface can refuse twice — Suggest and the create — so the newer wins.
  // The error of the kind that is showing. Inference is not here: the Suggest
  // button catches its own rejection and says so on itself, so feeding it to
  // this alert too would announce one refusal in two live regions -- and
  // React Query holds an error until its own mutation runs again, so a spent
  // create error would outrank the live inference one anyway.
  const failure =
    showing === 'agent' ? start.error : showing === 'linear' ? plan.error : create.error;
  // A create whose launch failed still wrote the task, and the refusal names
  // it. Remembering that is what stops a retry writing a second copy.
  const [written, setWritten] = useState<string | null>(null);

  /**
   * Name the task from its prose: title, branch, command and mode.
   *
   * A button rather than a gate. Inference shells out to a model and takes tens
   * of seconds, so the form must reach Save without it — and the fields it fills
   * stay editable after it, as every other field is.
   */
  const suggest = async () => {
    const inferred = await infer.mutateAsync({ project: chosen, draft });
    setCaptured((was) => ({
      ...was,
      title: inferred.title,
      branch: inferred.branch,
      command: inferred.command,
      taskMode: inferred.mode,
    }));
  };

  const startFreeAgent = async () => {
    await start.mutateAsync({
      project: chosen,
      branch,
      prompt: draft,
      mode,
      model,
      executeModel,
    });
    // Submitted, so the held copy is spent. Before the close, which unmounts the
    // dialog and would otherwise flush what is still in the field.
    release();
    close(false);
  };

  const planIssue = async (launch: boolean) => {
    if (written) return;
    try {
      await plan.mutateAsync({ project: chosen, issueId: issue, ...(launch ? { launch } : {}) });
    } catch (e) {
      // As a task create: the task exists and only its launch failed, so the
      // dialog says so and never offers to write it again.
      const taskId = e instanceof ApiError ? e.detail.taskId : undefined;
      // The task was written, so the prose that became it is spent -- only the
      // launch failed.
      if (typeof taskId === 'string') {
        setWritten(taskId);
        release();
      }
      throw e;
    }
    release();
    close(false);
  };

  const writeTask = async (launch: boolean) => {
    if (written) return;
    try {
      await create.mutateAsync({
        project: chosen,
        title: task.title.trim() || titleFromDraft(draft),
        // Both come from the prose when Suggest was never pressed, by the same
        // deterministic rule the notebook falls back to -- see `branchFromDraft`.
        branch: task.branch.trim() || branchFromDraft(draft),
        content: task.content,
        command: task.command,
        mode: task.mode,
        priority: task.priority,
        model: task.model,
        executeModel: task.executeModel,
        base: task.base,
        // `follows` is left out: a new task has nothing to follow yet, and an
        // explicit empty list would be a needless field on the wire.
        ...(launch ? { launch } : {}),
      });
    } catch (e) {
      // The task exists and is on the desk; only the launch failed. Keep the
      // dialog open to say so, but never offer to write it again.
      const taskId = e instanceof ApiError ? e.detail.taskId : undefined;
      if (typeof taskId === 'string') {
        setWritten(taskId);
        release();
      }
      throw e;
    }
    release();
    close(false);
  };

  return (
    <Dialog label="New work" onClose={() => close(false)}>
      <DialogHeader title="New work" onClose={() => close(false)} />

      <Capture
        names={names}
        inView={inView}
        project={chosen}
        setProject={(name) =>
          // The issue belongs to the project it was picked under, so it does
          // not survive a move to another one.
          patch({ project: name, issue: '' })
        }
        kind={showing}
        setKind={(next) => patch({ kind: next })}
        hasLinear={hasLinear}
        issue={issue}
        setIssue={(next) => patch({ issue: next })}
        draft={draft}
        setDraft={setDraft}
        branch={branch}
        setBranch={(next) => patch({ branch: next })}
        branches={branches}
        model={model}
        setModel={(next) => patch({ model: next })}
        executeModel={executeModel}
        setExecuteModel={(next) => patch({ executeModel: next })}
        mode={mode}
        setMode={(next) => patch({ mode: next })}
        bucket={bucket}
        attached={attached}
        onAttached={(a, at) =>
          // The bucket the image was actually uploaded under, so a first
          // attach keeps the one its ref embeds.
          setCaptured((was) => ({ ...was, bucket: at, attached: [...was.attached, a] }))
        }
        onRemoved={(image) =>
          setCaptured((was) => ({
            ...was,
            attached: was.attached.filter((w) => w.url !== image.url),
            draft: withoutRef(was.draft, image),
          }))
        }
        task={task}
        patchTask={patchTask}
        onSuggest={suggest}
        busy={busy}
      />

      {failure && (
        <p className={styles.error} role="alert" data-testid="new-work-error">
          {written
            ? `${failure.message}. The task is saved as ${written}; start it from the canvas.`
            : failure.message}
        </p>
      )}
      <DialogFooter>
        {busy && <Spinner />}
        {/* An ordinary affordance of the field, not a remedy for a restore, so
            it is never conditional on one having happened. Cancel holds what was
            typed -- this is the explicit discard. */}
        <button type="button" onClick={() => release()}>
          Clear
        </button>
        <button type="button" onClick={() => close(false)}>
          Cancel
        </button>
        {showing === 'agent' ? (
          <AppButton
            variant="primary"
            disabled={busy || !chosen || !draft.trim() || !branch.trim()}
            onClick={() => startFreeAgent()}
          >
            Start
          </AppButton>
        ) : showing === 'linear' ? (
          // `mael linear plan` takes the issue and nothing else.
          <>
            <AppButton
              disabled={busy || !issue.trim() || written !== null}
              onClick={() => planIssue(false)}
            >
              Save
            </AppButton>
            <AppButton
              variant="primary"
              disabled={busy || !issue.trim() || written !== null}
              onClick={() => planIssue(true)}
            >
              Start
            </AppButton>
          </>
        ) : (
          // The prose is the only field a task needs: inference no longer gates
          // the submit, so both buttons sit on the one surface.
          <>
            <AppButton
              disabled={busy || !chosen || !draft.trim() || written !== null}
              onClick={() => writeTask(false)}
            >
              Save
            </AppButton>
            <AppButton
              variant="primary"
              disabled={busy || !chosen || !draft.trim() || written !== null}
              onClick={() => writeTask(true)}
            >
              Start
            </AppButton>
          </>
        )}
      </DialogFooter>
    </Dialog>
  );
}

/** The one surface: what the work is, where it runs, and the task's own fields. */
function Capture({
  names,
  inView,
  project,
  setProject,
  kind,
  setKind,
  hasLinear,
  issue,
  setIssue,
  draft,
  setDraft,
  branch,
  setBranch,
  branches,
  model,
  setModel,
  executeModel,
  setExecuteModel,
  mode,
  setMode,
  bucket,
  attached,
  onAttached,
  onRemoved,
  task,
  patchTask,
  onSuggest,
  busy,
}: {
  names: string[];
  /** The projects the canvas is drawing, which the radios offer. */
  inView: string[];
  project: string;
  setProject: (name: string) => void;
  kind: Kind;
  setKind: (kind: Kind) => void;
  hasLinear: boolean;
  issue: string;
  setIssue: (issue: string) => void;
  draft: string;
  setDraft: (draft: string) => void;
  branch: string;
  setBranch: (branch: string) => void;
  branches: string[];
  model: string;
  setModel: (model: string) => void;
  executeModel: string;
  setExecuteModel: (model: string) => void;
  mode: PermissionMode;
  setMode: (mode: PermissionMode) => void;
  bucket: string;
  attached: Attachment[];
  /** The image, and the bucket it was stored under, which the dialog then keeps. */
  onAttached: (attachment: Attachment, bucket: string) => void;
  onRemoved: (image: Attachment) => void;
  /** The task the fields below write, for the `task` kind. */
  task: TaskDraft;
  patchTask: (fields: Partial<TaskDraft>) => void;
  /** Returns its promise, so the button it sits on can show the wait. */
  onSuggest: () => Promise<void>;
  busy: boolean;
}) {
  // Document-global, so nothing else on the page may share them.
  const kindName = useId();
  const draftId = useId();
  const branchOptions = useMemo(() => branches.map((value) => ({ value })), [branches]);
  return (
    <>
      <ProjectField names={names} inView={inView} project={project} setProject={setProject} />

      <fieldset className={dialog.kinds}>
        <legend>Kind</legend>
        {(
          [
            ['task', 'Task'],
            ['agent', 'Free agent'],
            // Only where a Linear team is configured. Everything else about
            // this kind is walled off in `LinearFields`.
            ...(hasLinear ? ([['linear', 'Linear']] as const) : []),
          ] as const
        ).map(([value, label]) => (
          <label key={value} className={dialog.kind}>
            <input
              type="radio"
              name={kindName}
              value={value}
              checked={kind === value}
              onChange={() => setKind(value)}
            />
            <span>{label}</span>
          </label>
        ))}
      </fieldset>

      {kind === 'linear' && <LinearFields project={project} issue={issue} setIssue={setIssue} />}

      {/* Title first, matching the task editor's order — see
          `tasklist/TaskFields.tsx`. */}
      {kind === 'task' && <TaskTitleField draft={task} onChange={patchTask} />}

      {kind !== 'linear' && (
        <div className={dialog.field}>
          <label htmlFor={draftId}>What needs doing?</label>
          <AttachField
            project={project}
            bucket={bucket}
            attached={attached}
            onAttach={(a) => {
              onAttached(a, bucket);
              setDraft(draft ? `${draft}\n\n${a.markdown}` : a.markdown);
            }}
            onRemove={onRemoved}
          >
            <textarea
              id={draftId}
              className={styles.draft}
              rows={8}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
          </AttachField>
        </div>
      )}

      {/* A free agent has no task to derive a branch from, so it names one
          itself. A task's branch is its own field below. */}
      {kind === 'agent' && (
        <>
          <label className={dialog.field}>
            <span>Branch</span>
            <ComboBox value={branch} options={branchOptions} onChange={setBranch} />
          </label>
          <label className={dialog.field}>
            <span>Mode</span>
            <ModeSelect mode={mode} onChange={setMode} />
          </label>
          <div className={dialog.row}>
            <label className={dialog.field}>
              <span>Model</span>
              <ModelSelect model={model} onChange={setModel} />
            </label>
            <label className={dialog.field}>
              <span>Execute Model</span>
              <ExecuteModelSelect model={executeModel} onChange={setExecuteModel} />
            </label>
          </div>
        </>
      )}

      {/* The task's own fields, on the same surface as the prose. Composed from
          the editor's own parts, so the two cannot drift on what a task's
          fields are -- see `tasklist/TaskFields.tsx`. */}
      {kind === 'task' && (
        <>
          <div className={styles.branchRow}>
            <label className={dialog.field}>
              <span>Branch</span>
              <input value={task.branch} onChange={(e) => patchTask({ branch: e.target.value })} />
            </label>
            {/* Inference is slow and optional, so it is a button beside the
                field it fills rather than a step in the way. An `AppButton`,
                which owns the life of its click: the returned promise is what
                puts the wait on the control that started it. */}
            <AppButton disabled={busy || !draft.trim()} onClick={onSuggest}>
              Suggest
            </AppButton>
          </div>
          <PlanningLevelField
            command={task.command}
            mode={task.mode}
            onChange={(fields) => patchTask(fields)}
          />
          <TaskAdvancedFields draft={task} onChange={patchTask} />
        </>
      )}
    </>
  );
}
