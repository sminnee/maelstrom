import type { AgentId, DocumentId, RequestId, TranscriptItemId } from './ids';

// Render-ready items. The wire never carries raw stream-json: the server's
// normaliser turns the daemon's events into these.

/** The tools whose `tool_use` raises a question and a plan review. */
export const QUESTION_TOOL = 'AskUserQuestion';
export const PLAN_TOOL = 'ExitPlanMode';

interface Base {
  id: TranscriptItemId;
  /** When the item's source event happened, ISO 8601 — not when it was normalised. */
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

/**
 * Where the agent's context was compacted — see `CONTEXT.md`, "Compact
 * boundary". The one event that says a compact finished rather than was
 * refused, so the Compact button waits on this item's arrival.
 *
 * Both figures are `0` when the boundary carried no counts.
 */
export interface CompactItem extends Base {
  type: 'compact';
  /** `manual` or `auto`; the normaliser defaults an unreadable one to `manual`. */
  trigger: string;
  preTokens: number;
  postTokens: number;
}

/**
 * The continuation prompt a compact injects to carry the conversation on.
 *
 * Written by the harness rather than the operator, and thousands of characters
 * long, so it folds: read as an ordinary turn it buries the boundary it sits
 * under. Same treatment as {@link SkillItem}, for the same reason.
 */
export interface CompactSummaryItem extends Base {
  type: 'compact_summary';
  markdown: string;
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

/** Events the agent host dropped before the server could read them stood here. */
export interface GapItem extends Base {
  type: 'gap';
  droppedEvents: number;
}

/** A skill the agent loaded, and the body the harness injected for it. */
export interface SkillItem extends Base {
  type: 'skill';
  skill: string;
  markdown: string;
}

/** A shell command the host ran for a `!` line, and what it wrote. */
export interface ShellItem extends Base {
  type: 'shell';
  command: string;
  output: string;
  status: ToolCallStatus;
}

export type TranscriptItem =
  | MessageItem
  | ToolCallItem
  | QuestionItem
  | PermissionRequestItem
  | PlanReviewItem
  | TurnResultItem
  | CompactItem
  | CompactSummaryItem
  | SystemItem
  | ErrorItem
  | GapItem
  | SkillItem
  | ShellItem;

export interface Transcript {
  agentId: AgentId;
  items: TranscriptItem[];
  /** True when the daemon's 200-event window dropped older items. */
  truncatedBefore: boolean;
}
