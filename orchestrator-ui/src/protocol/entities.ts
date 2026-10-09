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
export type PrState =
  'merged' | 'ci-failed' | 'ci-running' | 'conflict' | 'checks-unreadable' | 'unknown' | 'ready';

/** A worktree's env state — see **Env state** in `CONTEXT.md`. */
export type EnvStateName = 'running' | 'partial' | 'stopped';

/** One per-worktree service. `url` is `''` unless the service is web-facing. */
export interface WorktreeService {
  name: string;
  optional: boolean;
  running: boolean;
  url: string;
}

export interface WorktreeEnv {
  state: EnvStateName;
  /**
   * The declared per-worktree services in config order, optional ones included.
   * A Procfile project lists its tracked services and a synthetic `app`.
   */
  services: WorktreeService[];
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
  /** Commits on the open PR, or `null` when there is no PR. */
  prCommits: number | null;
  /**
   * Commits pushed with no open PR, or `null` when a PR is open. A branch
   * whose PR merged and which now waits on a new one reads its remote commits
   * here.
   */
  pushedCommits: number | null;
  /** The PR's browse URL, or `''` when there is no PR or no browse URL for the repo. */
  prUrl: string;
  /** How close the PR is to merging, or `''` when there is no PR. */
  prState: PrState | '';
  prDraft: boolean;
  /** When the PR merged, ISO 8601, or `''` when there is no PR or it is open. */
  prMergedAt: string;
  /**
   * The **PR match**: `'match'` when the local `HEAD` is the PR head, `'differ'`
   * when not, `''` with no open PR or an unknown head.
   */
  prMatch: '' | 'match' | 'differ';
  /** Absent from a server older than the UI; read it as stopped with no services. */
  env?: WorktreeEnv;
  sessionCount: number;
  /** The `cmux://` link to the pane of the worktree's first terminal, or `''` with none. */
  shellUrl: string;
}

/** One **Dirty file**: its path and `git status`'s letter, `?` when untracked. */
export interface ChangedFile {
  path: string;
  status: string;
}

/** One commit on a worktree's branch that its base does not have. */
export interface BranchCommit {
  sha: string;
  shortSha: string;
  subject: string;
  /** The message after its subject line; `''` when there is none. */
  body: string;
  author: string;
  /** ISO 8601, the author date. */
  date: string;
  filesChanged: number;
}

/** `GET /api/worktrees/{id}/changes`: what the Changes tab can show. */
export interface WorktreeChanges {
  dirtyFiles: ChangedFile[];
  /** The **Base** the commits are measured against: `main` when the base was pruned. */
  base: string;
  /** Oldest first. */
  commits: BranchCommit[];
}

export interface DiffLine {
  kind: 'context' | 'add' | 'remove';
  text: string;
  /** `null` on an added line. */
  oldLine: number | null;
  /** `null` on a removed line. */
  newLine: number | null;
}

export interface DiffHunk {
  /** The `@@ -a,b +c,d @@` line as git wrote it. */
  header: string;
  lines: DiffLine[];
}

/** One file of `GET /api/worktrees/{id}/diff`. */
export interface FileDiff {
  path: string;
  /** The path before a rename, else `null`. */
  oldPath: string | null;
  status: 'added' | 'modified' | 'deleted' | 'renamed';
  binary: boolean;
  additions: number;
  deletions: number;
  /** The file passed the line cap, so `hunks` holds its first lines only. */
  truncated: boolean;
  hunks: DiffHunk[];
}

/** One comment of `POST /api/worktrees/{id}/comments`. See CONTEXT.md, "Change comment". */
export interface ChangeComment {
  /** Made by the client, which keys its own list on it. */
  id: string;
  /** `uncommitted`, `branch` or a commit sha, as the diff route names it. */
  rev: string;
  path: string;
  /** `old` only when no selected row has a new line number. */
  side: 'new' | 'old';
  startLine: number;
  endLine: number;
  /** The selected rows, each with its sign, as the user saw them. */
  lines: string[];
  body: string;
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
  /** The model the session switches to when its plan is approved; '' = no switch. */
  executeModel: string;
  /** The Registered PR: its number, 0 for none, and its URL. */
  prNumber: number;
  prUrl: string;
  content: string;
  log: TaskLogEntry[];
  created: string;
  updated: string;
  /** Derived by the backend: may maelstrom launch it now. */
  actionable: boolean;
  /**
   * When the task's first agent started, ISO 8601, or `''` when none has.
   * Unlike an agent's, it outlives the agent.
   */
  startedAt: string;
  /** The task's **Landing**: `null` until the task is `done`. */
  landing: TaskLanding | null;
  /** The ids of the comms the task feeds, e.g. `c3`. */
  comms: string[];
}

/** One deploy environment's reading for a done task. `unknown` never counts as reached. */
export type EnvLandingState = 'landed' | 'not_yet' | 'unknown';

/**
 * A done task's **Landing**: the highest step it reached — `done`, `merged`,
 * then each deploy step — and each deploy step's state. `envs` is in step
 * order, and empty for a task with no Registered PR.
 */
export interface TaskLanding {
  status: string;
  envs: Record<string, EnvLandingState>;
}

/**
 * One **Comm**: something to tell people outside the team when work lands. `taskIds` are the
 * wire ids of the tasks whose `comms` name it, derived by the server.
 */
export interface Comm {
  id: string;
  title: string;
  content: string;
  recipients: string[];
  createdAt: string;
  /** `''` while the comm is open. */
  closedAt: string;
  /** Free text. See `CONTEXT.md`, "Comm category". */
  category: string;
  /** The project a task made from the comm goes to, or `''`. */
  project: string;
  taskIds: TaskId[];
}

/** From `agent_model.py`: every state is observed from an event, never inferred. */
export type AgentState =
  | 'idle'
  | 'processing'
  /** The turn ended while a background subagent still runs. */
  | 'delegating'
  /** The turn ended while a background shell still runs, and no subagent does. */
  | 'background'
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
  /**
   * What the agent said it is doing, from the `<note>` it wrote. Its own
   * summary of the work, where `lastMessage` is whatever prose ended a turn.
   *
   * `''` for a subagent, which writes none, and for an agent that has not
   * written one.
   */
  lastNote: string;
  /** When the agent wrote that note, ISO 8601; `''` until it writes one. */
  lastNoteAt: string;
  /**
   * When the orchestrator started the agent, or first saw one it did not
   * start, ISO 8601, or `''` with no Agent record. A resume keeps it.
   */
  startedAt: string;
  costUsd: number;
  /**
   * Tokens the session has consumed, summed over its turns: how much work it
   * has done. Not how full its context is — a turn re-reads its prompt from
   * cache each request, so this counts the same context again and again and
   * runs past any window. `contextTokens` is the figure for that.
   *
   * A subagent reports its own figure here, summed off its `assistant`
   * events. It is not in its parent's: a subagent emits no `result`, so the
   * parent's total covers the parent's own requests alone.
   */
  totalTokens: number;
  /** What this agent's subagents consumed, summed. Disjoint from
   * `totalTokens`, which is the agent's own; their sum is the tree's total. */
  subagentTokens: number;
  /**
   * What the agent's prompt last held, off the newest `assistant` event: a
   * level, not a total, so it falls when the agent compacts. This is what a
   * reader deciding whether to compact wants.
   *
   * `0` for a subagent, whose context is the parent's prompt.
   */
  contextTokens: number;
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
  /** The background shells running now, oldest first. Empty for a subagent. */
  backgroundShells: BackgroundShell[];
}

/** One background shell an agent runs: the task id and its `Bash` description. */
export interface BackgroundShell {
  id: string;
  description: string;
}

/** One entry on the desk: a task or a free agent the canvas keeps drawing. */
export interface DeskEntry {
  id: DeskId;
  addedAt: string;
}

/** One rolling budget the account spends against. */
export interface UsageWindow {
  /**
   * How much of the window is spent, 0 to 1. The source quantises this to
   * whole percent, so a reader shows what it was given and nothing finer.
   */
  utilization: number;
  /** When the window rolls over, unix seconds. */
  resetsAt: number;
}

/**
 * The account's budget, as the host last heard it.
 *
 * A window nobody has reported is `null`. `at` is when the reading was taken:
 * a reading only arrives while an agent takes a turn, so one taken long ago is
 * stale and a reader needs the time to know whether to trust it.
 */
export interface HostUsage {
  fiveHour: UsageWindow | null;
  sevenDay: UsageWindow | null;
  at: string;
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
  /** The account's budget, or `null` until a reading arrives. */
  usage: HostUsage | null;
}
