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
  const [project, setProject] = useState('');
  const chosen = project || names[0] || '';
  const [kind, setKind] = useState<Kind>('task');
  const [issue, setIssue] = useState('');
  // Only a project that names a Linear team can plan a Linear issue.
  const hasLinear = projects.data?.projects.find((p) => p.name === chosen)?.hasLinear ?? false;
  // A project change can take the chosen kind off the board, so the form falls
  // back to the one every project has rather than sitting on a dead kind.
  const showing: Kind = kind === 'linear' && !hasLinear ? 'task' : kind;
  const [draft, setDraft] = useState('');
  const [branch, setBranch] = useState('');
  // A free agent's own mode and model. A task takes its mode from inference,
  // and leaves its model unset: `docs/guide/planning.md` asks for that on
  // execute drafts, and step 2's Advanced section is where one is chosen.
  const [mode, setMode] = useState<PermissionMode>(MODES[0]);
  const [model, setModel] = useState<string>(DEFAULT_MODEL);
  // New work has no id to group its images under, so the dialog mints one and
  // keeps it for its whole life -- including across the step 1 to 2 move, so an
  // image attached to the prose is the same bucket as one attached to the
  // content. `git add -A` on the task's own commit sweeps the files in.
  const [attached, setAttached] = useState<Attachment[]>([]);
  const [bucket] = useState(() => `draft-${Math.random().toString(36).slice(2, 10)}`);
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
      if (typeof taskId === 'string') setWritten(taskId);
      throw e;
    }
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
      if (typeof taskId === 'string') setWritten(taskId);
      throw e;
    }
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
          setProject={(name) => {
            setProject(name);
            // The issue belongs to the project it was picked under, so it does
            // not survive a move to another one.
            setIssue('');
          }}
          kind={showing}
          setKind={setKind}
          hasLinear={hasLinear}
          issue={issue}
          setIssue={setIssue}
          draft={draft}
          setDraft={setDraft}
          branch={branch}
          setBranch={setBranch}
          branches={branches}
          model={model}
          setModel={setModel}
          mode={mode}
          setMode={setMode}
          bucket={bucket}
          attached={attached}
          onAttached={(a) => setAttached((was) => [...was, a])}
          onRemoved={(image) => {
            setAttached((was) => was.filter((w) => w.url !== image.url));
            setDraft((was) => withoutRef(was, image));
          }}
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
  onAttached: (attachment: Attachment) => void;
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
              onAttached(a);
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
