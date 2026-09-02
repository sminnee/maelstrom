import type { Attention } from '../protocol/attention';
import { isOpen } from '../protocol/attention';
import type { Agent, Phase, Task } from '../protocol/entities';
import type { World } from '../protocol/events';
import type { TaskId } from '../protocol/ids';
import type { NodeState } from '../protocol/phase';
import { nodeState } from '../protocol/phase';
import type { Filters, GroupBy } from './filters';

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

/** Everything the canvas draws, derived from the world plus client state. */
export function deriveGraph(world: World, opts: GraphOptions): Graph {
  const tasks = Object.values(world.tasks)
    .filter((t) => t.status !== 'template')
    .filter((t) => !opts.filters.project || t.project === opts.filters.project)
    .filter((t) => !opts.filters.branch || t.branch === opts.filters.branch)
    .filter((t) => !opts.filters.hideDone || (t.status !== 'done' && t.status !== 'cancelled'))
    .sort((a, b) => a.created.localeCompare(b.created) || a.id.localeCompare(b.id));

  const groups = new Map<string, GraphGroup>();
  const nodes: GraphNode[] = [];
  for (const task of tasks) {
    const agent = agentForTask(world, task.id);
    const attention = openAttentionFor(world, task, agent);
    const groupId = opts.groupBy === 'project' ? task.project : `${task.project}/${task.branch}`;
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

function makeGroup(world: World, task: Task, id: string, kind: GroupBy): GraphGroup {
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
