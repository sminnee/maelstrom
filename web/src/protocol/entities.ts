import type { AgentId, ProjectId, RequestId, TaskId, WorktreeId } from './ids';

/** Which of the four stages a task's work is in. Derived from the task's `command`. */
export type Phase = 'shaping' | 'planning' | 'executing' | 'finalising';

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

/** Mirrors a task file's frontmatter plus two backend-derived fields. */
export interface Task {
  id: TaskId;
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
  /** Derived by the backend from `command`. */
  phase: Phase;
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
  phase: Phase;
  exitCode: number | null;
  pendingRequestId: RequestId | null;
}
