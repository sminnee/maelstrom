// A desk id says what the entry names. The desk holds notebook tasks and free
// agents, whose own ids share no shape, so the kind is carried in the id
// rather than inferred from it.
import type { AgentId, TaskId } from './ids';

/** What a desk id can name. */
export type DeskKind = 'task' | 'agent';

const KINDS: DeskKind[] = ['task', 'agent'];

/** The desk id for a notebook task. */
export function deskIdForTask(taskId: TaskId): string {
  return `task:${taskId}`;
}

/** The desk id for a free agent — one with no task. */
export function deskIdForAgent(agentId: AgentId): string {
  return `agent:${agentId}`;
}

/**
 * What a desk id names: its kind, then the entity's own id. `null` when the id
 * carries no kind, or a kind the desk has no entity for.
 */
export function splitDeskId(deskId: string): { kind: DeskKind; id: string } | null {
  const sep = deskId.indexOf(':');
  if (sep < 0) return null;
  const kind = deskId.slice(0, sep) as DeskKind;
  if (!KINDS.includes(kind)) return null;
  return { kind, id: deskId.slice(sep + 1) };
}
