import type { Attention } from './attention';
import type { Comment, Document } from './documents';
import type { Agent, DeskEntry, Project, Task, Worktree } from './entities';
import type {
  AgentId,
  AttentionId,
  CommentId,
  DeskId,
  DocumentId,
  ProjectId,
  Seq,
  TaskId,
  TranscriptItemId,
  WorktreeId,
} from './ids';
import type { Transcript, TranscriptItem } from './transcript';

export interface World {
  projects: Record<ProjectId, Project>;
  worktrees: Record<WorktreeId, Worktree>;
  tasks: Record<TaskId, Task>;
  agents: Record<AgentId, Agent>;
  documents: Record<DocumentId, Document>;
  comments: Record<CommentId, Comment>;
  attention: Record<AttentionId, Attention>;
  desk: Record<DeskId, DeskEntry>;
}

export type EntityKind = keyof EntityMap;

export interface EntityMap {
  project: Project;
  worktree: Worktree;
  task: Task;
  agent: Agent;
  document: Document;
  comment: Comment;
  attention: Attention;
  desk: DeskEntry;
}

export const ENTITY_KINDS: readonly EntityKind[] = [
  'project',
  'worktree',
  'task',
  'agent',
  'document',
  'comment',
  'attention',
  'desk',
];

/** Which `World` key each entity kind lives under. */
export const WORLD_KEY: { [K in EntityKind]: keyof World } = {
  project: 'projects',
  worktree: 'worktrees',
  task: 'tasks',
  agent: 'agents',
  document: 'documents',
  comment: 'comments',
  attention: 'attention',
  desk: 'desk',
};

export type UpsertEvent = {
  [K in EntityKind]: { type: 'upsert'; kind: K; entity: EntityMap[K] };
}[EntityKind];

export interface RemoveEvent {
  type: 'remove';
  kind: EntityKind;
  id: string;
}

export interface SnapshotEvent {
  type: 'snapshot';
  world: World;
  transcripts: Record<AgentId, Transcript>;
}

export interface TranscriptAppendEvent {
  type: 'transcript.append';
  agentId: AgentId;
  item: TranscriptItem;
}

export interface TranscriptUpdateEvent {
  type: 'transcript.update';
  agentId: AgentId;
  itemId: TranscriptItemId;
  patch: Partial<TranscriptItem>;
}

/** The agent host's event window dropped items older than this transcript holds. */
export interface TranscriptTruncatedEvent {
  type: 'transcript.truncated';
  agentId: AgentId;
}

export interface ErrorEvent {
  type: 'error';
  message: string;
  agentId?: AgentId;
}

export type ServerEvent =
  | SnapshotEvent
  | UpsertEvent
  | RemoveEvent
  | TranscriptAppendEvent
  | TranscriptUpdateEvent
  | TranscriptTruncatedEvent
  | ErrorEvent;

/** One event as it travels: seq-stamped, replayable, describes the world. */
export interface EventFrame {
  seq: Seq;
  ts: string;
  event: ServerEvent;
}
