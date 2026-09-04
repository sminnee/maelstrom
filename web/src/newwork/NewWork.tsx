import { useId, useMemo, useState } from 'react';
import { useStartAgent } from '../api/agents';
import { ApiError } from '../api/http';
import { useProjects } from '../api/projects';
import { useCreateTask, useInferTask } from '../api/tasks';
import type { TaskDraft } from '../tasklist/TaskFields';
import { TaskFields } from '../tasklist/TaskFields';
import { useWorktrees } from '../api/worktrees';
import { useAppStore } from '../store/store';
import { AppButton } from '../ui/AppButton';
import { Dialog, DialogFooter, DialogHeader } from '../ui/Dialog';
import { Spinner } from '../ui/Spinner';
import dialog from '../ui/Dialog.module.css';
import styles from './NewWork.module.css';

/** What the user is starting: a task in the notebook, or an agent tied to none. */
type Kind = 'task' | 'agent';

/**
 * One form for both kinds of new work: a task, or a free agent.
 *
 * Two steps in one dialog — see `docs/dev/orchestrator-ui.md`. A free agent
 * has no step 2: the branch and the prompt are all it needs.
 */
export function NewWork() {
  const close = useAppStore((s) => s.setNewWorkOpen);
  const projects = useProjects();
  const worktrees = useWorktrees();
  const infer = useInferTask();
  const create = useCreateTask();
  const start = useStartAgent();

  const names = projects.data?.projects.map((p) => p.name) ?? [];
  const [project, setProject] = useState('');
  const chosen = project || names[0] || '';
  const [kind, setKind] = useState<Kind>('task');
  const [draft, setDraft] = useState('');
  const [branch, setBranch] = useState('');
  const [model, setModel] = useState('');
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

  const busy = infer.isPending || create.isPending || start.isPending;
  // The error of the step that is showing. React Query holds a mutation's
  // error until that same mutation runs again, so a fixed precedence would
  // let a refused start outlive the step that raised it.
  const failure = task ? create.error : kind === 'agent' ? start.error : infer.error;
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
      model,
    });
  };

  const startFreeAgent = async () => {
    await start.mutateAsync({
      project: chosen,
      branch,
      prompt: draft,
      mode: 'normal',
      ...(model ? { model } : {}),
    });
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
        <TaskFields draft={task} onChange={(patch) => setTask({ ...task, ...patch })} />
      ) : (
        <Capture
          names={names}
          project={chosen}
          setProject={setProject}
          kind={kind}
          setKind={setKind}
          draft={draft}
          setDraft={setDraft}
          branch={branch}
          setBranch={setBranch}
          branches={branches}
          model={model}
          setModel={setModel}
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
        ) : kind === 'agent' ? (
          <AppButton
            variant="primary"
            disabled={busy || !chosen || !draft.trim() || !branch.trim()}
            onClick={() => startFreeAgent()}
          >
            Start
          </AppButton>
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
  draft,
  setDraft,
  branch,
  setBranch,
  branches,
  model,
  setModel,
}: {
  names: string[];
  project: string;
  setProject: (name: string) => void;
  kind: Kind;
  setKind: (kind: Kind) => void;
  draft: string;
  setDraft: (draft: string) => void;
  branch: string;
  setBranch: (branch: string) => void;
  branches: string[];
  model: string;
  setModel: (model: string) => void;
}) {
  // Document-global, so nothing else on the page may share them.
  const kindName = useId();
  const branchList = useId();
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

      <label className={dialog.field}>
        <span>What needs doing?</span>
        <textarea
          className={styles.draft}
          rows={8}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
      </label>

      {/* A free agent has no task to derive a branch from, so it names one
          itself. A task's branch is inferred at the next step instead. */}
      {kind === 'agent' && (
        <>
          <label className={dialog.field}>
            <span>Branch</span>
            <input list={branchList} value={branch} onChange={(e) => setBranch(e.target.value)} />
            <datalist id={branchList}>
              {branches.map((b) => (
                <option key={b} value={b} />
              ))}
            </datalist>
          </label>
          <label className={dialog.field}>
            <span>Model</span>
            <input value={model} onChange={(e) => setModel(e.target.value)} />
          </label>
        </>
      )}
    </>
  );
}
