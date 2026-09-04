import type { Task, TaskMode } from '../protocol/entities';

/** A task as the list carries it: everything but the prose. */
export type TaskRow = Omit<Task, 'content' | 'log'>;

/**
 * The fields of a task the UI may edit, all optional. Only the keys present
 * are written, matching the notebook's own "an omitted field is left as-is"
 * contract. Status is folder-derived, so it moves through its own route.
 */
export interface TaskEdit {
  title?: string;
  content?: string;
  branch?: string;
  command?: string;
  mode?: TaskMode;
  priority?: string;
  model?: string;
}

/** The server's refusal codes, plus the two the client makes for itself. */
export const ERROR_CODES = [
  'unknown_id',
  'agent_exited',
  'not_waiting',
  'stale_request',
  'wrong_wait_kind',
  'stale_version',
  'invalid',
  'not_implemented',
  'timeout',
  'transport',
] as const;

export type ErrorCode = (typeof ERROR_CODES)[number];

/** A code off the wire, or `invalid` for one this client does not know. */
export function errorCode(raw: unknown): ErrorCode {
  return (ERROR_CODES as readonly unknown[]).includes(raw) ? (raw as ErrorCode) : 'invalid';
}

/** The kinds a change notice may name. */
export type NoticeKind =
  'project' | 'worktree' | 'task' | 'agent' | 'attention' | 'document' | 'desk';

export interface ChangeNotice {
  kind: NoticeKind;
  ids: string[];
}
