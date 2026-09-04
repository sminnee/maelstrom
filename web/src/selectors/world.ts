import type { TaskRow } from '../api/types';
import type { Attention } from '../protocol/attention';
import type { Document } from '../protocol/documents';
import type { Agent, DeskEntry, Project, Worktree } from '../protocol/entities';
import type {
  AgentId,
  AttentionId,
  DeskId,
  DocumentId,
  ProjectId,
  TaskId,
  WorktreeId,
} from '../protocol/ids';

/** A document as the list carries it: everything but the markdown. */
export type DocumentRow = Omit<Document, 'markdown'>;

/**
 * The world as the views read it: the seven tables the list routes serve,
 * keyed by id. Tasks and documents are their slim rows; the prose of one
 * comes from its detail route. Every selector takes this, so a full `World`
 * from the wire satisfies it too.
 */
export interface WorldView {
  projects: Record<ProjectId, Project>;
  worktrees: Record<WorktreeId, Worktree>;
  tasks: Record<TaskId, TaskRow>;
  agents: Record<AgentId, Agent>;
  documents: Record<DocumentId, DocumentRow>;
  attention: Record<AttentionId, Attention>;
  desk: Record<DeskId, DeskEntry>;
}

export function emptyWorldView(): WorldView {
  return {
    projects: {},
    worktrees: {},
    tasks: {},
    agents: {},
    documents: {},
    attention: {},
    desk: {},
  };
}

/** A list as a table keyed by id. */
export function byId<T extends { id: string }>(items: readonly T[] | undefined): Record<string, T> {
  const table: Record<string, T> = {};
  for (const item of items ?? []) table[item.id] = item;
  return table;
}
