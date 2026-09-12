import { useId, useMemo, useState } from 'react';
import { withoutRef, type Attachment } from '../api/attachments';
import { AttachField } from '../ui/AttachField';
import { useStartAgent } from '../api/agents';
import { ApiError } from '../api/http';
import { useProjects } from '../api/projects';
import { useCreateLinearTask } from '../api/linear';
import { useCreateTask, useInferTask } from '../api/tasks';
import type { PermissionMode } from '../protocol/modes';
import { MODES } from '../protocol/modes';
import { DEFAULT_MODEL, UNSET_MODEL } from '../protocol/models';
import type { TaskDraft } from '../tasklist/TaskFields';
import { ModeSelect, ModelSelect, TaskFields } from '../tasklist/TaskFields';
import { useWorktrees } from '../api/worktrees';
import { useAppStore } from '../store/store';
import { AppButton } from '../ui/AppButton';
import { ComboBox } from '../ui/ComboBox';
import { Dialog, DialogFooter, DialogHeader } from '../ui/Dialog';
import { LinearFields } from './LinearFields';
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
 * Everything step 1 captures, and the whole of what the dialog holds.
 *
 * One value rather than a key per field, because the prose, its attachments and
 * their bucket only make sense together — see `useRetained`. Step 2 is not held:
 * the dialog reopens on step 1 and re-infers.
 */
interface Step1 {
  project: string;
  kind: Kind;
  issue: string;
  draft: string;
  branch: string;
  mode: PermissionMode;
  model: string;
  attached: Attachment[];
  /**
   * Groups this dialog's images in the task repo. Minted once and then held: a
   * re-minted bucket would send the next image to another directory, and would
   * stop `withoutRef` matching the refs already in the prose.
   */
  bucket: string;
}

/**
 * A dialog with nothing in it yet.
 *
 * A free agent's own mode and model default here. A task takes its mode from
 * inference and leaves its model unset: `docs/guide/planning.md` asks for that
 * on execute drafts, and step 2's Advanced section is where one is chosen.
 */
const initialStep1: Step1 = {
  project: '',
  kind: 'task',
  issue: '',
  draft: '',
  branch: '',
  mode: MODES[0],
  model: DEFAULT_MODEL,
  attached: [],
  bucket: '',
};

/** A bucket for a dialog that has none held yet. */
const mintBucket = () => `draft-${Math.random().toString(36).slice(2, 10)}`;

/**
 * One form for every kind of new work — see `Kind`.
 *
 * Two steps in one dialog — see `docs/dev/orchestrator-ui.md`. Only a task has
 * a step 2; a free agent and a Linear plan need nothing beyond step 1.
 */
export function NewWork() {
  const close = useAppStore((s) => s.setNewWorkOpen);
  const projects = useProjects();
  const worktrees = useWorktrees();
  const infer = useInferTask();
  const create = useCreateTask();
  const start = useStartAgent();
  const plan = useCreateLinearTask();

  const names = projects.data?.projects.map((p) => p.name) ?? [];
  // Everything step 1 captures, held as one value -- see `ui/useRetained.ts`.
  // The prose, its attachments and their bucket cannot be held separately: an
  // image's ref lives in the prose, and `withoutRef` matches on a ref that
  // embeds the bucket, so a re-minted bucket would make removing a thumbnail a
  // silent no-op and send the agent a link to an image it never got.
  const [step1, setStep1, release] = useRetained(retainedKey.newWork(), initialStep1);
  const { kind, issue, draft, branch, mode, model, attached } = step1;
  // One bucket for the dialog's whole life, both steps included. State with a
  // lazy initialiser, so it is settled once on mount: an expression like
  // `held || mintBucket()` in the render yields a new directory every pass until
  // one is committed, and the server refuses an upload with no bucket at all.
  // `useRetained` reads storage in its own initialiser, so a held bucket is
  // already here to be reused rather than replaced.
  const [bucket] = useState(() => step1.bucket || mintBucket());
  const patch = (fields: Partial<Step1>) => setStep1((was) => ({ ...was, ...fields }));
  // A held project the world no longer has is dropped rather than carried: the
  // fallback below picks the *first* project, so a stale name would silently
  // write the work against a different one.
  const project = step1.project && names.includes(step1.project) ? step1.project : '';
  const chosen = project || names[0] || '';
  // Only a project that names a Linear team can plan a Linear issue.
  const hasLinear = projects.data?.projects.find((p) => p.name === chosen)?.hasLinear ?? false;
  // A project change can take the chosen kind off the board, so the form falls
  // back to the one every project has rather than sitting on a dead kind.
  const showing: Kind = kind === 'linear' && !hasLinear ? 'task' : kind;
  const setDraft = (next: string | ((was: string) => string)) =>
    setStep1((was) => ({
      ...was,
      draft: typeof next === 'function' ? next(was.draft) : next,
    }));
  /** The inferred task, once step 2 is reached. `null` means step 1. */
  const [task, setTask] = useState<TaskDraft | null>(null);

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

  const busy = infer.isPending || create.isPending || start.isPending || plan.isPending;
  // The error of the step that is showing. React Query holds a mutation's
  // error until that same mutation runs again, so a fixed precedence would
  // let a refused start outlive the step that raised it.
  const failure = task
    ? create.error
    : showing === 'agent'
      ? start.error
      : showing === 'linear'
        ? plan.error
        : infer.error;
  // A create whose launch failed still wrote the task, and the refusal names
  // it. Remembering that is what stops a retry writing a second copy.
  const [written, setWritten] = useState<string | null>(null);

  const next = async () => {
    const inferred = await infer.mutateAsync({ project: chosen, draft });
    setTask({
      title: inferred.title,
      content: draft,
      branch: inferred.branch,
      command: inferred.command,
      mode: inferred.mode,
      priority: 'medium',
      model: UNSET_MODEL,
    });
  };

  const startFreeAgent = async () => {
    await start.mutateAsync({
      project: chosen,
      branch,
      prompt: draft,
      mode,
      model,
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
    if (!task || written) return;
    try {
      await create.mutateAsync({ project: chosen, ...task, ...(launch ? { launch } : {}) });
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
      <DialogHeader title={task ? 'Task details' : 'New work'} onClose={() => close(false)} />

      {task ? (
        <TaskFields
          draft={task}
          onChange={(patch) => setTask({ ...task, ...patch })}
          project={chosen}
          bucket={bucket}
        />
      ) : (
        <Capture
          names={names}
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
          mode={mode}
          setMode={(next) => patch({ mode: next })}
          bucket={bucket}
          attached={attached}
          onAttached={(a, at) =>
            // The bucket the image was actually uploaded under, so a first
            // attach keeps the one its ref embeds.
            setStep1((was) => ({ ...was, bucket: at, attached: [...was.attached, a] }))
          }
          onRemoved={(image) =>
            setStep1((was) => ({
              ...was,
              attached: was.attached.filter((w) => w.url !== image.url),
              draft: withoutRef(was.draft, image),
            }))
          }
        />
      )}

      {failure && (
        <p className={styles.error} role="alert" data-testid="new-work-error">
          {written
            ? `${failure.message}. The task is saved as ${written}; start it from the canvas.`
            : failure.message}
        </p>
      )}
      <DialogFooter>
        {busy && <Spinner />}
        {task && (
          <button type="button" disabled={busy} onClick={() => setTask(null)}>
            Back
          </button>
        )}
        {/* An ordinary affordance of the field, not a remedy for a restore, so
            it is never conditional on one having happened. Cancel holds what was
            typed -- this is the explicit discard. */}
        {!task && (
          <button type="button" onClick={() => release()}>
            Clear
          </button>
        )}
        <button type="button" onClick={() => close(false)}>
          Cancel
        </button>
        {task ? (
          <>
            <AppButton disabled={busy || written !== null} onClick={() => writeTask(false)}>
              Save
            </AppButton>
            <AppButton
              variant="primary"
              disabled={busy || written !== null}
              onClick={() => writeTask(true)}
            >
              Start
            </AppButton>
          </>
        ) : showing === 'agent' ? (
          <AppButton
            variant="primary"
            disabled={busy || !chosen || !draft.trim() || !branch.trim()}
            onClick={() => startFreeAgent()}
          >
            Start
          </AppButton>
        ) : showing === 'linear' ? (
          // No step 2: `mael linear plan` takes the issue and nothing else.
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
          <AppButton
            variant="primary"
            disabled={busy || !chosen || !draft.trim()}
            onClick={() => next()}
          >
            Next
          </AppButton>
        )}
      </DialogFooter>
    </Dialog>
  );
}

/** Step 1: what the work is, where it runs, and which kind it is. */
function Capture({
  names,
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
  mode,
  setMode,
  bucket,
  attached,
  onAttached,
  onRemoved,
}: {
  names: string[];
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
  mode: PermissionMode;
  setMode: (mode: PermissionMode) => void;
  bucket: string;
  attached: Attachment[];
  /** The image, and the bucket it was stored under, which the dialog then keeps. */
  onAttached: (attachment: Attachment, bucket: string) => void;
  onRemoved: (image: Attachment) => void;
}) {
  // Document-global, so nothing else on the page may share them.
  const kindName = useId();
  const draftId = useId();
  const branchOptions = useMemo(() => branches.map((value) => ({ value })), [branches]);
  return (
    <>
      <label className={dialog.field}>
        <span>Project</span>
        <select value={project} onChange={(e) => setProject(e.target.value)}>
          {names.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </label>

      <fieldset className={styles.kinds}>
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
          <label key={value} className={styles.kind}>
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
          itself. A task's branch is inferred at the next step instead. */}
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
          <label className={dialog.field}>
            <span>Model</span>
            <ModelSelect model={model} onChange={setModel} />
          </label>
        </>
      )}
    </>
  );
}
