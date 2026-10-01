import type { AgentId, CommentId, DocumentId, RequestId, TaskId } from './ids';

export type DocumentKind = 'plan' | 'tasks' | 'pr' | 'review' | 'verification' | 'other';

export type DocumentStatus =
  | 'draft'
  | 'awaiting-review'
  | 'approved'
  | 'changes-requested'
  | 'superseded'
  /** Its review ended with nobody answering — see `CONTEXT.md`, "Stale prompt". */
  | 'stale';

/**
 * Where a document came from, so the backend knows how approve and request
 * changes map back. The UI never reads this.
 */
export type DocumentSource =
  | { type: 'plan_review'; requestId: RequestId; planFilePath: string }
  /**
   * One file a `<doc-file>` tag named. `filename` is the worktree-relative
   * path and the document's identity; `fileId` is the file registry's id, and
   * null when the file could not be read.
   */
  | { type: 'draft_file'; fileId: string | null; filename: string }
  /** A plan read back from the notebook. Its review is over — see `CONTEXT.md`, "Attached document". */
  | { type: 'attached'; path: string };

/** The review group a document is a member of — see `CONTEXT.md`, "Review group". */
export interface DocumentGroup {
  id: string;
  title: string;
  /** The member's place in the tag, which for a task set is the chain's order. */
  position: number;
}

export interface Document {
  id: DocumentId;
  agentId: AgentId;
  taskId: TaskId;
  kind: DocumentKind;
  title: string;
  markdown: string;
  version: number;
  status: DocumentStatus;
  source: DocumentSource;
  group: DocumentGroup;
}

/**
 * Where a comment sits. The W3C TextQuoteSelector trio (`quote`, `prefix`,
 * `suffix`) is canonical; `start`/`end` cache offsets into that version's
 * markdown source.
 */
export interface Anchor {
  quote: string;
  prefix: string;
  suffix: string;
  start: number;
  end: number;
}

export interface Comment {
  id: CommentId;
  documentId: DocumentId;
  version: number;
  author: 'user' | AgentId;
  anchor: Anchor;
  body: string;
  resolved: boolean;
  createdAt: string;
}
