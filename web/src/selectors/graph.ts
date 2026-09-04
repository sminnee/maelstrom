import type { Attention } from '../protocol/attention';
import { deskIdForTask } from '../protocol/deskId';
import { isOpen } from '../protocol/attention';
import type { Agent, Phase, Task } from '../protocol/entities';
import type { World } from '../protocol/events';
import type { TaskId } from '../protocol/ids';
import type { NodeState } from '../protocol/phase';
import { nodeState } from '../protocol/phase';
import type { Filters, GroupBy } from './filters';
import { branchKey } from './filters';

export interface GraphNode {
  id: TaskId;
  task: Task;
  agent: Agent | undefined;
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

/** The agent to show for a task: a live one first, else the most recent. */
export function agentForTask(world: World, taskId: TaskId): Agent | undefined {
  const agents = Object.values(world.agents).filter((a) => a.taskId === taskId);
  return agents.find((a) => a.state !== 'exited') ?? agents[agents.length - 1];
}

export function openAttentionFor(world: World, task: Task, agent?: Agent): Attention[] {
  return Object.values(world.attention)
    .filter((a) => isOpen(a) && (a.taskId === task.id || (agent && a.agentId === agent.id)))
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
  // The canvas draws the desk: what the user has put on it, and nothing else.
  const tasks = filteredTasks(world, opts.filters).filter((t) => deskIdForTask(t.id) in world.desk);

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
      task,
      agent,
      state: nodeState(task, agent, attention),
      phase: task.phase,
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
