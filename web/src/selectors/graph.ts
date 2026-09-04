import type { Attention } from '../protocol/attention';
import { deskIdForAgent, deskIdForTask } from '../protocol/deskId';
import { isOpen } from '../protocol/attention';
import type { Agent, Phase, Task, Worktree } from '../protocol/entities';
import type { World } from '../protocol/events';
import type { TaskId } from '../protocol/ids';
import type { NodeState } from '../protocol/phase';
import { nodeState } from '../protocol/phase';
import type { Filters, GroupBy } from './filters';
import { branchKey } from './filters';

/** What a node stands for: a notebook task, or an agent with no task. */
export type NodeKind = 'task' | 'freeAgent';

export interface GraphNode {
  /** The wire task id, or the agent id for a freeAgent. */
  id: string;
  kind: NodeKind;
  /** Absent on a freeAgent, which stands for no task. */
  task: Task | undefined;
  agent: Agent | undefined;
  /** Where a freeAgent gets its lane, branch and name. */
  worktree: Worktree | undefined;
  state: NodeState;
  phase: Phase;
  groupId: string;
  attention: Attention[];
  /** One line under the title saying why the node needs the user, else ''. */
  reason: string;
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

/** The agent to show for a task: a live one first, else the most recent. */
export function agentForTask(world: World, taskId: TaskId): Agent | undefined {
  const agents = Object.values(world.agents).filter((a) => a.taskId === taskId);
  return agents.find((a) => a.state !== 'exited') ?? agents[agents.length - 1];
}

export function openAttentionFor(world: World, task: Task | undefined, agent?: Agent): Attention[] {
  return Object.values(world.attention)
    .filter(
      (a) => isOpen(a) && ((task && a.taskId === task.id) || (agent && a.agentId === agent.id)),
    )
    .sort((a, b) => a.raisedAt.localeCompare(b.raisedAt));
}

/**
 * The tasks the canvas filters allow, whether or not they are on the desk.
 *
 * The attention chip counts against this rather than the drawn nodes: an
 * agent blocked on a task the user has not put on the desk still needs them.
 */
export function filteredTasks(world: World, filters: Filters): Task[] {
  return Object.values(world.tasks)
    .filter((t) => t.status !== 'template')
    .filter((t) => !filters.project || t.project === filters.project)
    .filter((t) => !filters.branch || branchKey(t.project, t.branch) === filters.branch)
    .sort((a, b) => a.created.localeCompare(b.created) || a.id.localeCompare(b.id));
}

/** Everything the canvas draws, derived from the world plus client state. */
export function deriveGraph(world: World, opts: GraphOptions): Graph {
  // Drawn when it is on the desk, or its agent is live. The liveness half is
  // what puts running work on the canvas before the server's auto-join has
  // round-tripped, and what keeps it there if the entry is removed early.
  const tasks = filteredTasks(world, opts.filters).filter(
    (t) => deskIdForTask(t.id) in world.desk || isLive(agentForTask(world, t.id)),
  );

  const groups = new Map<string, GraphGroup>();
  const nodes: GraphNode[] = [];
  for (const task of tasks) {
    const agent = agentForTask(world, task.id);
    const attention = openAttentionFor(world, task, agent);
    const groupId = groupIdFor(task, opts.groupBy);
    if (!groups.has(groupId)) groups.set(groupId, makeGroup(world, task, groupId, opts.groupBy));
    groups.get(groupId)!.nodeIds.push(task.id);
    nodes.push({
      id: task.id,
      kind: 'task',
      task,
      agent,
      worktree: undefined,
      state: nodeState(task, agent, attention),
      phase: task.phase,
      groupId,
      attention,
      reason: attention[0]?.summary ?? '',
    });
  }

  // An agent with no task draws in its own right. One linked to a task drew
  // above, so nothing appears twice.
  for (const agent of Object.values(world.agents)) {
    if (agent.taskId) continue;
    if (!isLive(agent) && !(deskIdForAgent(agent.id) in world.desk)) continue;
    const worktree = world.worktrees[agent.worktreeId];
    if (!allowsAgent(opts.filters, agent, worktree)) continue;
    const attention = openAttentionFor(world, undefined, agent);
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
      state: nodeState(undefined, agent, attention),
      phase: agent.phase,
      groupId,
      attention,
      reason: attention[0]?.summary ?? '',
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
  world: World,
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

function groupIdFor(task: Task, kind: GroupBy): string {
  switch (kind) {
    case 'project':
      return task.project;
    case 'branch':
      return `${task.project}/${task.branch}`;
    case 'none':
      return 'all';
  }
}

/** With `none`, one unlabelled group holds every node and the canvas draws no lane for it. */
function makeGroup(world: World, task: Task, id: string, kind: GroupBy): GraphGroup {
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
  const worktree = Object.values(world.worktrees).find(
    (w) => w.project === task.project && w.branch === task.branch && !w.isClosed,
  );
  return {
    id,
    kind,
    label: task.branch || '(no branch)',
    sublabel: worktree?.nato ?? '',
    nodeIds: [],
  };
}
