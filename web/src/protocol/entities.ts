import type { AgentId, DeskId, ProjectId, RequestId, TaskId, WorktreeId } from './ids';

/**
 * Which of the four stages a task's work is in, named as the imperative of the
 * work itself. Read from the task's `command`; never sent on the wire.
 */
export type Phase = 'shape' | 'plan' | 'build' | 'land';

/** The folder a task sits in. Mirrors the notebook's six statuses. */
export type TaskStatus = 'todo' | 'in-progress' | 'blocked' | 'done' | 'cancelled' | 'template';

export type TaskMode = 'plan' | 'auto' | 'normal';

export interface Project {
  id: ProjectId;
  name: string;
  stackTip: string;
}

/** Mirrors one row of `mael --json list-all`. */
export interface Worktree {
  id: WorktreeId;
  project: ProjectId;
  nato: string;
  path: string;
  branch: string;
  base: string;
  isClosed: boolean;
  dirtyFiles: number;
  localCommits: number;
  prNumber: number | null;
  appUrl: string;
  appRunning: boolean;
  sessionCount: number;
}

export interface TaskStep {
  text: string;
  done: boolean;
}

export interface TaskLogEntry {
  ts: string;
  text: string;
}

/**
 * Mirrors a task file's frontmatter plus the fields the backend derives.
 *
 * `id` is the wire id, `<project>/<notebook id>`; `notebookId` is the bare id
 * the notebook itself uses. Notebook ids repeat across projects, so only the
 * qualified one is unique in the world.
 */
export interface Task {
  id: TaskId;
  notebookId: string;
  project: ProjectId;
  title: string;
  status: TaskStatus;
  command: string;
  mode: TaskMode;
  branch: string;
  parent: string;
  follows: TaskId[];
  priority: string;
  model: string;
  base: string;
  content: string;
  steps: TaskStep[];
  log: TaskLogEntry[];
  created: string;
  updated: string;
  /** Derived by the backend: may maelstrom launch it now. */
  actionable: boolean;
}

/** From `agent_model.py`: every state is observed from an event, never inferred. */
export type AgentState =
  | 'idle'
  | 'processing'
  | 'awaiting-permission'
  | 'awaiting-question'
  | 'awaiting-plan-review'
  | 'exited';

/** Mirrors `build_agent_row` plus what links the agent to the rest of the world. */
export interface Agent {
  id: AgentId;
  state: AgentState;
  session: string;
  cwd: string;
  model: string;
  waitingOn: string;
  lastMessage: string;
  costUsd: number;
  taskId: TaskId;
  project: ProjectId;
  worktreeId: WorktreeId;
  exitCode: number | null;
  pendingRequestId: RequestId | null;
}

/** One entry on the desk: a task or a free agent the canvas keeps drawing. */
export interface DeskEntry {
  id: DeskId;
  addedAt: string;
}
