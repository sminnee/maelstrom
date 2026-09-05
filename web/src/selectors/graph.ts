import type { Attention } from '../protocol/attention';
import { deskIdForAgent, deskIdForTask } from '../protocol/deskId';
import { isOpen } from '../protocol/attention';
import type { TaskRow } from '../api/types';
import type { Agent, Phase, Worktree } from '../protocol/entities';
import type { WorldView } from './world';
import type { TaskId } from '../protocol/ids';
import { phaseForCommand } from '../protocol/phase';
import type { Progress } from '../protocol/progress';
import { progressOf } from '../protocol/progress';
import type { Filters, GroupBy } from './filters';
import { branchKey } from './filters';

/** What a node stands for: a notebook task, or an agent with no task. */
export type NodeKind = 'task' | 'freeAgent';

export interface GraphNode {
  /** The wire task id, or the agent id for a freeAgent. */
  id: string;
  kind: NodeKind;
  /** Absent on a freeAgent, which stands for no task. */
  task: TaskRow | undefined;
  agent: Agent | undefined;
  /** Where a freeAgent gets its lane, branch and name. */
  worktree: Worktree | undefined;
  /** The one reading of the node's state: how it draws, its words, its drift. */
  progress: Progress;
  /** Null on a freeAgent: with no task there is no command to read a phase from. */
  phase: Phase | null;
  groupId: string;
  attention: Attention[];
  /** One line under the title saying why the node needs the user, else ''. */
  reason: string;
  /**
   * Does the node have to name its own project. False when something else on
   * screen already does: the lane header when grouped by project, the filter
   * when filtered to one. The node's width is scarce, so it never repeats.
   */
  showProject: boolean;
}

export interface GraphGroup {
  id: string;
  kind: GroupBy;
  label: string;
  /** The worktree holding a branch, when grouping by branch. */
  sublabel: string;
  nodeIds: TaskId[];
}

export interface GraphEdge {
  id: string;
  source: TaskId;
  target: TaskId;
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  groups: GraphGroup[];
}

export interface GraphOptions {
  groupBy: GroupBy;
  filters: Filters;
}

/**
 * What a node is called: a task's title, else the worktree a free agent runs
 * in. An agent whose worktree the world has not read yet has neither.
 */
export function nodeTitle(node: GraphNode): string {
  if (node.task) return node.task.title;
  const { worktree } = node;
  if (!worktree) return 'Agent';
  return worktree.branch ? `${worktree.nato} · ${worktree.branch}` : worktree.nato;
}

/** Whether an agent is still running. An exited one draws nothing by itself. */
export function isLive(agent: Agent | undefined): boolean {
  return agent !== undefined && agent.state !== 'exited';
}

/**
 * One agent per task: the live one, else the last that ran. Built once for
 * every task at a time, so a pass over the world is O(tasks + agents), not
 * O(tasks × agents). A subagent carries its parent's task and is never the
 * pick: the parent's tab lists it.
 */
export function agentsByTask(world: WorldView): Map<TaskId, Agent> {
  const live = new Map<TaskId, Agent>();
  const last = new Map<TaskId, Agent>();
  for (const agent of Object.values(world.agents)) {
    if (!agent.taskId || agent.parent) continue;
    last.set(agent.taskId, agent);
    if (agent.state !== 'exited' && !live.has(agent.taskId)) live.set(agent.taskId, agent);
  }
  return new Map([...last, ...live]);
}

/** The open attention items, oldest first, keyed by task id and by agent id. */
function openAttentionIndex(world: WorldView): {
  byTask: Map<TaskId, Attention[]>;
  byAgent: Map<string, Attention[]>;
} {
  const byTask = new Map<TaskId, Attention[]>();
  const byAgent = new Map<string, Attention[]>();
  const open = Object.values(world.attention)
    .filter(isOpen)
    .sort((a, b) => a.raisedAt.localeCompare(b.raisedAt));
  for (const item of open) {
    if (item.taskId) listAt(byTask, item.taskId).push(item);
    if (item.agentId) listAt(byAgent, item.agentId).push(item);
  }
  return { byTask, byAgent };
}

/** The list under `key`, made on first use. */
function listAt<K, V>(map: Map<K, V[]>, key: K): V[] {
  let list = map.get(key);
  if (!list) {
    list = [];
    map.set(key, list);
  }
  return list;
}

function attentionFrom(
  index: ReturnType<typeof openAttentionIndex>,
  task: TaskRow | undefined,
  agent: Agent | undefined,
): Attention[] {
  const items = new Map<string, Attention>();
  for (const item of task ? (index.byTask.get(task.id) ?? []) : []) items.set(item.id, item);
  for (const item of agent ? (index.byAgent.get(agent.id) ?? []) : []) items.set(item.id, item);
  return [...items.values()].sort((a, b) => a.raisedAt.localeCompare(b.raisedAt));
}

/**
 * The tasks the canvas filters allow, whether or not they are on the desk.
 *
 * The attention chip counts against this rather than the drawn nodes: an
 * agent blocked on a task the user has not put on the desk still needs them.
 */
export function filteredTasks(world: WorldView, filters: Filters): TaskRow[] {
  return Object.values(world.tasks)
    .filter((t) => t.status !== 'template')
    .filter((t) => !filters.project || t.project === filters.project)
    .filter((t) => !filters.branch || branchKey(t.project, t.branch) === filters.branch)
    .sort((a, b) => a.created.localeCompare(b.created) || a.id.localeCompare(b.id));
}

/** Everything the canvas draws, derived from the world plus client state. */
export function deriveGraph(world: WorldView, opts: GraphOptions): Graph {
  // Drawn when it is on the desk, or its agent is live. The liveness half is
  // what puts running work on the canvas before the server's auto-join has
  // round-tripped, and what keeps it there if the entry is removed early.
  const agents = agentsByTask(world);
  const attentionIndex = openAttentionIndex(world);
  const worktreeByBranch = openWorktreesByBranch(world);
  const tasks = filteredTasks(world, opts.filters).filter(
    (t) => deskIdForTask(t.id) in world.desk || isLive(agents.get(t.id)),
  );

  const groups = new Map<string, GraphGroup>();
  const nodes: GraphNode[] = [];
  for (const task of tasks) {
    const agent = agents.get(task.id);
    const attention = attentionFrom(attentionIndex, task, agent);
    const groupId = groupIdFor(task, opts.groupBy);
    if (!groups.has(groupId)) {
      groups.set(groupId, makeGroup(world, task, groupId, opts.groupBy, worktreeByBranch));
    }
    groups.get(groupId)!.nodeIds.push(task.id);
    nodes.push({
      id: task.id,
      kind: 'task',
      task,
      agent,
      worktree: agent ? world.worktrees[agent.worktreeId] : undefined,
      progress: progressOf(task, agent, attention),
      phase: phaseForCommand(task.command),
      groupId,
      attention,
      reason: attention[0]?.summary ?? '',
      showProject: opts.groupBy !== 'project' && !opts.filters.project,
    });
  }

  // An agent with no task draws in its own right. One linked to a task drew
  // above, so nothing appears twice. A subagent never draws: it is reached
  // through its parent's session tab.
  for (const agent of Object.values(world.agents)) {
    if (agent.taskId || agent.parent) continue;
    if (!isLive(agent) && !(deskIdForAgent(agent.id) in world.desk)) continue;
    const worktree = world.worktrees[agent.worktreeId];
    if (!allowsAgent(opts.filters, agent, worktree)) continue;
    const attention = attentionFrom(attentionIndex, undefined, agent);
    const groupId = groupIdForAgent(agent, worktree, opts.groupBy);
    if (!groups.has(groupId)) {
      groups.set(groupId, makeAgentGroup(world, agent, worktree, groupId, opts.groupBy));
    }
    groups.get(groupId)!.nodeIds.push(agent.id);
    nodes.push({
      id: agent.id,
      kind: 'freeAgent',
      task: undefined,
      agent,
      worktree,
      progress: progressOf(undefined, agent, attention),
      phase: null,
      groupId,
      attention,
      reason: attention[0]?.summary ?? '',
      showProject: opts.groupBy !== 'project' && !opts.filters.project,
    });
  }

  const visible = new Set(nodes.map((n) => n.id));
  const edges: GraphEdge[] = [];
  for (const task of tasks) {
    for (const followed of task.follows) {
      if (!visible.has(followed)) continue;
      edges.push({ id: `${followed}->${task.id}`, source: followed, target: task.id });
    }
  }

  return {
    nodes,
    edges,
    groups: [...groups.values()].sort((a, b) => a.id.localeCompare(b.id)),
  };
}

/**
 * Whether the filters keep a free agent. It has no task to filter on, so the
 * worktree it runs in answers for its project and its branch.
 */
function allowsAgent(filters: Filters, agent: Agent, worktree: Worktree | undefined): boolean {
  const project = agent.project || worktree?.project || '';
  if (filters.project && project !== filters.project) return false;
  if (filters.branch && branchKey(project, worktree?.branch ?? '') !== filters.branch) return false;
  return true;
}

function groupIdForAgent(agent: Agent, worktree: Worktree | undefined, kind: GroupBy): string {
  const project = agent.project || worktree?.project || '';
  switch (kind) {
    case 'project':
      return project;
    case 'branch':
      return `${project}/${worktree?.branch ?? ''}`;
    case 'none':
      return 'all';
  }
}

/** A free agent's lane, named by its worktree rather than by a task. */
function makeAgentGroup(
  world: WorldView,
  agent: Agent,
  worktree: Worktree | undefined,
  id: string,
  kind: GroupBy,
): GraphGroup {
  if (kind === 'none') return { id, kind, label: '', sublabel: '', nodeIds: [] };
  const project = agent.project || worktree?.project || '';
  if (kind === 'project') {
    return { id, kind, label: world.projects[project]?.name ?? project, sublabel: '', nodeIds: [] };
  }
  return {
    id,
    kind,
    label: worktree?.branch || '(no branch)',
    sublabel: worktree?.nato ?? '',
    nodeIds: [],
  };
}

function groupIdFor(task: TaskRow, kind: GroupBy): string {
  switch (kind) {
    case 'project':
      return task.project;
    case 'branch':
      return `${task.project}/${task.branch}`;
    case 'none':
      return 'all';
  }
}

/** The open worktree on each branch, keyed by `branchKey`. */
function openWorktreesByBranch(world: WorldView): Map<string, Worktree> {
  const index = new Map<string, Worktree>();
  for (const w of Object.values(world.worktrees)) {
    const key = branchKey(w.project, w.branch);
    if (!w.isClosed && !index.has(key)) index.set(key, w);
  }
  return index;
}

/** With `none`, one unlabelled group holds every node and the canvas draws no lane for it. */
function makeGroup(
  world: WorldView,
  task: TaskRow,
  id: string,
  kind: GroupBy,
  worktreeByBranch: Map<string, Worktree>,
): GraphGroup {
  if (kind === 'none') return { id, kind, label: '', sublabel: '', nodeIds: [] };
  if (kind === 'project') {
    return {
      id,
      kind,
      label: world.projects[task.project]?.name ?? task.project,
      sublabel: '',
      nodeIds: [],
    };
  }
  const worktree = worktreeByBranch.get(branchKey(task.project, task.branch));
  return {
    id,
    kind,
    label: task.branch || '(no branch)',
    sublabel: worktree?.nato ?? '',
    nodeIds: [],
  };
}
