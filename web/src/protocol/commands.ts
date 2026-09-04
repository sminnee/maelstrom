import type { Anchor } from './documents';
import type { AgentId, CommentId, DeskId, DocumentId, ProjectId, RequestId, TaskId } from './ids';

export type Command =
  | {
      type: 'agent.approve';
      agentId: AgentId;
      requestId: RequestId;
      updatedInput?: Record<string, unknown>;
    }
  | { type: 'agent.deny'; agentId: AgentId; requestId: RequestId; reason: string }
  | {
      type: 'agent.answer';
      agentId: AgentId;
      requestId: RequestId;
      answers: Record<string, string>;
    }
  | { type: 'agent.say'; agentId: AgentId; text: string }
  | { type: 'agent.launch'; taskId: TaskId; model?: string }
  | { type: 'agent.stop'; agentId: AgentId }
  | { type: 'agent.resume'; agentId: AgentId; text?: string }
  | { type: 'desk.add'; id: DeskId }
  | { type: 'desk.remove'; id: DeskId }
  | { type: 'document.approve'; documentId: DocumentId; version: number }
  | { type: 'document.requestChanges'; documentId: DocumentId; version: number; summary: string }
  | { type: 'comment.add'; documentId: DocumentId; version: number; anchor: Anchor; body: string }
  | { type: 'comment.resolve'; commentId: CommentId }
  | { type: 'task.create'; project: ProjectId; draft: string }
  | { type: 'shaping.start'; project: ProjectId; brief: string };

export type CommandType = Command['type'];

/** What each command's ack carries. Most carry nothing beyond the ack. */
export interface ResultMap {
  'agent.approve': Record<string, never>;
  'agent.deny': Record<string, never>;
  'agent.answer': Record<string, never>;
  'agent.say': Record<string, never>;
  'agent.launch': { agentId: AgentId };
  'agent.stop': Record<string, never>;
  'agent.resume': Record<string, never>;
  'desk.add': Record<string, never>;
  'desk.remove': Record<string, never>;
  'document.approve': Record<string, never>;
  'document.requestChanges': Record<string, never>;
  'comment.add': { commentId: CommentId };
  'comment.resolve': Record<string, never>;
  'task.create': { taskId: TaskId };
  'shaping.start': { agentId: AgentId; taskId: TaskId };
}

export type ResultFor<C extends Command> = ResultMap[C['type']];

/** Mirrors the daemon's own refusals. */
export type ErrorCode =
  | 'unknown_id'
  | 'agent_exited'
  | 'not_waiting'
  | 'stale_request'
  | 'wrong_wait_kind'
  | 'stale_version'
  | 'invalid';

export interface CommandError {
  code: ErrorCode;
  message: string;
}

export type Reply<C extends Command> =
  { ok: true; result: ResultFor<C> } | { ok: false; error: CommandError };
