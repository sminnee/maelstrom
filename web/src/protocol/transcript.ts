import type { AgentId, DocumentId, RequestId, TranscriptItemId } from './ids';

// Render-ready items. The wire never carries raw stream-json; the backend
// (or `normalise.ts` in front of the fake) turns the daemon's events into these.

interface Base {
  id: TranscriptItemId;
  ts: string;
}

export interface MessageItem extends Base {
  type: 'message';
  role: 'user' | 'assistant';
  markdown: string;
}

export type ToolCallStatus = 'pending' | 'running' | 'done' | 'error' | 'denied';

/** A `tool_use` block and its later `tool_result` merged into one item. */
export interface ToolCallItem extends Base {
  type: 'tool_call';
  toolUseId: string;
  tool: string;
  input: Record<string, unknown>;
  status: ToolCallStatus;
  output?: string;
  diff?: string;
}

export interface QuestionOption {
  label: string;
  description: string;
}

export interface Question {
  question: string;
  header: string;
  multiSelect: boolean;
  options: QuestionOption[];
}

export interface QuestionItem extends Base {
  type: 'question';
  requestId: RequestId;
  questions: Question[];
  /** Keyed by question text, as the daemon files them. */
  answers?: Record<string, string>;
  /** The wait ended with nobody answering — see `CONTEXT.md`, "Stale prompt". */
  stale?: true;
}

export interface PermissionRequestItem extends Base {
  type: 'permission_request';
  requestId: RequestId;
  tool: string;
  input: Record<string, unknown>;
  description: string;
  decision?: 'allow' | 'deny';
  reason?: string;
  /** The wait ended with nobody answering — see `CONTEXT.md`, "Stale prompt". */
  stale?: true;
}

export interface PlanReviewItem extends Base {
  type: 'plan_review';
  requestId: RequestId;
  documentId: DocumentId | null;
  decision?: 'approve' | 'deny';
  /** Why it was denied: the deny message, which the agent gets as its tool result. */
  reason?: string;
  /** The wait ended with nobody answering — see `CONTEXT.md`, "Stale prompt". */
  stale?: true;
}

export interface TurnResultItem extends Base {
  type: 'turn_result';
  subtype: string;
  costUsd: number;
  durationMs: number;
}

export interface SystemItem extends Base {
  type: 'system';
  subtype: 'init';
  sessionId: string;
  model: string;
}

export interface ErrorItem extends Base {
  type: 'error';
  message: string;
}

export type TranscriptItem =
  | MessageItem
  | ToolCallItem
  | QuestionItem
  | PermissionRequestItem
  | PlanReviewItem
  | TurnResultItem
  | SystemItem
  | ErrorItem;

export interface Transcript {
  agentId: AgentId;
  items: TranscriptItem[];
  /** True when the daemon's 200-event window dropped older items. */
  truncatedBefore: boolean;
}
