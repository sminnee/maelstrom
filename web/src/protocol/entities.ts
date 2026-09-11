import type { PermissionMode } from './modes';
import type { AgentId, DeskId, ProjectId, RequestId, TaskId, WorktreeId } from './ids';

/**
 * Which of the four stages a task's work is in, named as the imperative of the
 * work itself. Read from the task's `command`; never sent on the wire.
 */
export type Phase = 'shape' | 'plan' | 'build' | 'land';

/** The folder a task sits in. Mirrors the notebook's six statuses. */
export type TaskStatus = 'todo' | 'in-progress' | 'blocked' | 'done' | 'cancelled' | 'template';

export const TASK_STATUSES = [
  'todo',
  'in-progress',
  'blocked',
  'done',
  'cancelled',
  'template',
] as const satisfies readonly TaskStatus[];

export type TaskMode = PermissionMode;

export interface Project {
  id: ProjectId;
  name: string;
  stackTip: string;
  /** Whether the project names a Linear team in its `.maelstrom.yaml`. */
  hasLinear: boolean;
}

/** How close a pull request is to merging. Decided in Python — see **PR state** in `CONTEXT.md`. */
export type PrState = 'merged' | 'ci-failed' | 'ci-running' | 'conflict' | 'unknown' | 'ready';

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
  /** The PR's browse URL, or `''` when there is no PR or no browse URL for the repo. */
  prUrl: string;
  /** How close the PR is to merging, or `''` when there is no PR. */
  prState: PrState | '';
  prDraft: boolean;
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
  /** The parent's id for a subagent (whose own id is dotted, `X.1`); `''` for a top-level agent. */
  parent: AgentId | '';
  /** What the parent asked a subagent to do; `''` for a top-level agent. */
  description: string;
  state: AgentState;
  session: string;
  cwd: string;
  model: string;
  /** The mode the child last announced; `''` until its `system`/`init` arrives. */
  permissionMode: PermissionMode | '';
  waitingOn: string;
  lastMessage: string;
  /** When the agent last said that, ISO 8601; `''` until it has said anything. */
  lastMessageAt: string;
  costUsd: number;
  /**
   * Tokens the session has consumed, summed over its turns: how large the
   * conversation has grown. `0` for a subagent, which has no session of its
   * own — its size is counted in its parent's total.
   */
  totalTokens: number;
  taskId: TaskId;
  project: ProjectId;
  worktreeId: WorktreeId;
  exitCode: number | null;
  /**
   * Every ask the agent is blocked on, oldest first. Several can be open at
   * once — see `docs/dev/agent-daemon.md`, "A subagent's permission ask".
   */
  pendingRequestIds: RequestId[];
  /** The child's pid while it is alive; `null` before the spawn and after the exit. */
  pid: number | null;
}

/** One entry on the desk: a task or a free agent the canvas keeps drawing. */
export interface DeskEntry {
  id: DeskId;
  addedAt: string;
}

/**
 * Whether the agent host answers the server, and since when it has not. One
 * entity, id `agent-host`. The server never exits an agent for the host being
 * away, so this is how the app knows the agents it shows are the last known.
 */
export interface Host {
  id: 'agent-host';
  reachable: boolean;
  /** When `reachable` last changed. */
  since: string;
  /** The socket the server reaches the host on. */
  socket: string;
}
