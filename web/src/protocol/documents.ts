import type { AgentId, CommentId, DocumentId, RequestId, TaskId, TranscriptItemId } from './ids';

export type DocumentKind = 'plan' | 'tasks' | 'pr' | 'review' | 'other';

export type DocumentStatus =
  'draft' | 'awaiting-review' | 'approved' | 'changes-requested' | 'superseded';

/**
 * Where a document came from, so the backend knows how approve and request
 * changes map back. The UI never reads this.
 */
export type DocumentSource =
  | { type: 'plan_review'; requestId: RequestId; planFilePath: string }
  | { type: 'draft_files'; paths: string[] }
  | { type: 'pr'; number: number }
  | { type: 'message'; transcriptItemId: TranscriptItemId };

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
