import type { Attention } from '../protocol/attention';
import { deskIdForAgent, deskIdForTask } from '../protocol/deskId';
import { isOpen } from '../protocol/attention';
import type { TaskRow } from '../api/types';
import type { Agent, Operation, OperationKind, Phase, Worktree } from '../protocol/entities';
import type { WorldView } from './world';
import type { TaskId } from '../protocol/ids';
import { phaseForTask } from '../protocol/phase';
import type { Progress, Zone } from '../protocol/progress';
import { isWorking, progressOf, zoneForState } from '../protocol/progress';
import type { AgentStatusFilter, Filters } from './filters';
import { branchKey, matchesText, searching } from './filters';
import { byName } from './worktrees';
import { hasRegisteredPr } from './cardPr';

/**
 * What a node stands for: a notebook task, an agent with no task, or an
 * **Opening node** — work an operation is starting, before its agent or task
 * exists.
 */
export type NodeKind = 'task' | 'freeAgent' | 'opening';

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
  /** The zone an operation holds the node in, over the one its state reads. */
  zone?: Zone;
  /** An opening node's operation. */
  operation?: Operation;
}

/** The zone a node draws in: the one an operation holds it in, else its state's. */
export const nodeZone = (node: GraphNode): Zone => node.zone ?? zoneForState(node.progress.state);

export interface GraphGroup {
  /** The project's id: every lane is one project's strip. */
  id: string;
  label: string;
  nodeIds: TaskId[];
  /**
   * The open worktrees of the project that hold no node of the lane, in name
   * order. The lane draws each as an empty **Worktree box**.
   */
  emptyWorktrees: Worktree[];
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
  filters: Filters;
}

/**
 * What a node is called: a task's title, else the worktree a free agent runs
 * in. An agent whose worktree the world has not read yet has neither.
 */
export function nodeTitle(node: GraphNode): string {
  if (node.task) return node.task.title;
  const { worktree } = node;
  if (!worktree) return node.operation?.kind === 'plan' ? 'New plan' : 'Agent';
  return worktree.branch ? `${worktree.nato} · ${worktree.branch}` : worktree.nato;
}

/**
 * The id a node shows: a task's bare notebook id, because the lane already
 * names the project, else the free agent's own id — the **Failover id** rule
 * in `CONTEXT.md`.
 *
 * An agent id is already 8 characters, so nothing here truncates.
 */
export function nodeIdLine(node: GraphNode): string {
  if (node.operation) return node.operation.id;
  return node.task ? node.task.notebookId : node.id;
}

/**
 * The worktree a node's work is in. The agent knows where it runs, so its
 * worktree beats the branch index's guess.
 */
export function nodeWorktree(world: WorldView, node: GraphNode): Worktree | undefined {
  return node.worktree ?? (node.agent ? world.worktrees[node.agent.worktreeId] : undefined);
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

/** The tasks the canvas filters allow, whether or not they are on the desk. */
function filteredTasks(world: WorldView, filters: Filters): TaskRow[] {
  return Object.values(world.tasks)
    .filter((t) => t.status !== 'template')
    .filter((t) => !filters.project || t.project === filters.project)
    .filter((t) => !filters.branch || branchKey(t.project, t.branch) === filters.branch)
    .filter((t) => !filters.comm || t.comms.includes(filters.comm))
    .filter((t) => matchesText([t.id, t.notebookId, t.title], filters))
    .sort((a, b) => a.created.localeCompare(b.created) || a.id.localeCompare(b.id));
}

/**
 * The tasks and agents a running **Dismiss** names. They draw until it ends,
 * so a node does not vanish when its agent stops, before it leaves the desk.
 */
function dismissing(world: WorldView): Set<string> {
  const named = new Set<string>();
  for (const op of Object.values(world.operations ?? {})) {
    if (op.kind !== 'dismiss' || op.state !== 'running') continue;
    if (op.taskId) named.add(op.taskId);
    if (op.agentId) named.add(op.agentId);
  }
  return named;
}

/** The kinds that launch a task, when their steps include a launch. */
const LAUNCH_KINDS: ReadonlySet<OperationKind> = new Set(['launch', 'create', 'plan']);

/**
 * The tasks a running launch is starting. Each draws in the running zone, its
 * final cell, before its agent exists to put it there.
 */
function launching(world: WorldView): Set<string> {
  const named = new Set<string>();
  for (const op of Object.values(world.operations ?? {})) {
    if (!LAUNCH_KINDS.has(op.kind) || op.state !== 'running' || !op.taskId) continue;
    if (op.steps.some((s) => s.name === 'start_agent')) named.add(op.taskId);
  }
  return named;
}

/**
 * The operations that draw an **Opening node**: a free agent's start or a
 * Linear plan, running or ended badly and unseen, until the agent or task it
 * made draws as a node of its own.
 */
function openingOperations(world: WorldView, drawn: ReadonlySet<string>): Operation[] {
  return Object.values(world.operations ?? {})
    .filter((op) => op.kind === 'start' || op.kind === 'plan')
    .filter((op) => op.state === 'running' || (op.state !== 'done' && !op.seen))
    .filter((op) => !(op.agentId && drawn.has(op.agentId)) && !(op.taskId && drawn.has(op.taskId)))
    .sort((a, b) => a.startedAt.localeCompare(b.startedAt));
}

/** Everything the canvas draws, derived from the world plus client state. */
export function deriveGraph(world: WorldView, opts: GraphOptions): Graph {
  // Drawn when it is on the desk, its agent is live, or a Dismiss runs on it.
  // The liveness half is what puts running work on the canvas before the
  // server's auto-join has round-tripped, and what keeps it there if the
  // entry is removed early.
  const agents = agentsByTask(world);
  const attentionIndex = openAttentionIndex(world);
  const worktreeByBranch = openWorktreesByBranch(world);
  const midDelete = dismissing(world);
  const midCreate = launching(world);
  const tasks = filteredTasks(world, opts.filters)
    .filter(
      (t) => deskIdForTask(t.id) in world.desk || isLive(agents.get(t.id)) || midDelete.has(t.id),
    )
    .filter((t) => allowsAgentStatus(opts.filters.agentStatus, agents.get(t.id), t));

  const groups = new Map<string, GraphGroup>();
  const laneOf = (project: string) => {
    let lane = groups.get(project);
    if (!lane) {
      const label = world.projects[project]?.name ?? project;
      lane = { id: project, label, nodeIds: [], emptyWorktrees: [] };
      groups.set(project, lane);
    }
    return lane;
  };

  const nodes: GraphNode[] = [];
  for (const task of tasks) {
    const agent = agents.get(task.id);
    const attention = attentionFrom(attentionIndex, task, agent);
    const groupId = task.project;
    laneOf(groupId).nodeIds.push(task.id);
    // The agent knows which worktree it is in; the branch index only guesses.
    // The guess is what keeps a finished task naming the worktree — and so
    // the PR — after its agent has stopped.
    const worktree =
      (agent ? world.worktrees[agent.worktreeId] : undefined) ??
      worktreeByBranch.get(branchKey(task.project, task.branch));
    nodes.push({
      id: task.id,
      kind: 'task',
      task,
      agent,
      worktree,
      progress: progressOf(task, agent, attention, worktree?.gate ?? null),
      phase: phaseForTask(task, agent),
      groupId,
      attention,
      reason: attention[0]?.summary ?? '',
      ...(midCreate.has(task.id) ? { zone: 'running' as const } : {}),
    });
  }

  // An agent with no task draws in its own right. One linked to a task drew
  // above, so nothing appears twice. A subagent never draws: it is reached
  // through its parent's session tab.
  for (const agent of Object.values(world.agents)) {
    if (agent.taskId || agent.parent) continue;
    if (!isLive(agent) && !(deskIdForAgent(agent.id) in world.desk) && !midDelete.has(agent.id))
      continue;
    const worktree = world.worktrees[agent.worktreeId];
    if (!allowsAgent(opts.filters, agent, worktree)) continue;
    if (!allowsAgentStatus(opts.filters.agentStatus, agent, undefined)) continue;
    const attention = attentionFrom(attentionIndex, undefined, agent);
    const groupId = agent.project || worktree?.project || '';
    const node: GraphNode = {
      id: agent.id,
      kind: 'freeAgent',
      task: undefined,
      agent,
      worktree,
      progress: progressOf(undefined, agent, attention, worktree?.gate ?? null),
      phase: null,
      groupId,
      attention,
      reason: attention[0]?.summary ?? '',
    };
    if (!matchesText([agent.id, nodeTitle(node)], opts.filters)) continue;
    laneOf(groupId).nodeIds.push(agent.id);
    nodes.push(node);
  }

  // Work an operation is opening draws before it has a task or an agent of
  // its own. A status or text filter names states and words it has none of.
  const drawn = new Set(nodes.map((n) => n.id));
  const plain = (opts.filters.agentStatus ?? 'all') === 'all' && !searching(opts.filters);
  for (const op of plain && !opts.filters.comm ? openingOperations(world, drawn) : []) {
    if (opts.filters.project && op.project !== opts.filters.project) continue;
    const worktree = op.worktreeId ? world.worktrees[op.worktreeId] : undefined;
    if (
      opts.filters.branch &&
      branchKey(op.project, worktree?.branch ?? '') !== opts.filters.branch
    )
      continue;
    const id = `op:${op.id}`;
    laneOf(op.project).nodeIds.push(id);
    nodes.push({
      id,
      kind: 'opening',
      task: undefined,
      agent: undefined,
      worktree,
      progress: progressOf(undefined, undefined, [], null),
      phase: null,
      groupId: op.project,
      attention: [],
      reason: '',
      // A plan that does not launch ends as a task not yet started.
      zone:
        op.kind === 'plan' && !op.steps.some((s) => s.name === 'start_agent')
          ? 'notStarted'
          : 'running',
      operation: op,
    });
  }

  // A branch, status, text or comm filter hides nodes, so a worktree with no
  // node drawn may still hold work. An empty box would then say what is not true.
  const hidesNodes =
    opts.filters.branch ||
    opts.filters.comm ||
    (opts.filters.agentStatus ?? 'all') !== 'all' ||
    searching(opts.filters);
  if (!hidesNodes) listEmptyWorktrees(world, opts.filters, nodes, laneOf);

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
  // A comm links tasks, so an agent with none never matches one.
  if (filters.comm) return false;
  const project = agent.project || worktree?.project || '';
  if (filters.project && project !== filters.project) return false;
  if (filters.branch && branchKey(project, worktree?.branch ?? '') !== filters.branch) return false;
  return true;
}

/** The status filter groups agent states for the Desk's scan-level control. */
function allowsAgentStatus(
  filter: AgentStatusFilter | undefined,
  agent: Agent | undefined,
  task: TaskRow | undefined,
): boolean {
  switch (filter ?? 'all') {
    case 'all':
      return true;
    case 'working':
      return isWorking(agent);
    case 'idle':
      return agent?.state === 'idle' || agent?.state.startsWith('awaiting-') === true;
    case 'working-idle':
      return agent !== undefined && agent.state !== 'exited';
    case 'terminated':
      return agent?.state === 'exited';
    case 'planned':
      return agent === undefined;
    case 'has-pr':
      return hasRegisteredPr(task);
  }
}

/**
 * Give each project lane the open worktrees that hold none of its nodes.
 *
 * A project with no node gets its lane here, from the world, so its empty
 * boxes still draw. Never `_main` — see `docs/dev/orchestrator-ui.md`. A node
 * carries the worktree object itself, so that is what the match is on.
 */
function listEmptyWorktrees(
  world: WorldView,
  filters: Filters,
  nodes: GraphNode[],
  laneOf: (project: string) => GraphGroup,
): void {
  const held = new Set(nodes.map((n) => n.worktree));
  for (const worktree of Object.values(world.worktrees).sort(byName)) {
    if (worktree.isClosed || worktree.nato === '_main' || held.has(worktree)) continue;
    if (filters.project && worktree.project !== filters.project) continue;
    laneOf(worktree.project).emptyWorktrees.push(worktree);
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
