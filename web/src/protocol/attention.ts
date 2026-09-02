import type { AgentId, AttentionId, DocumentId, RequestId, TaskId } from './ids';

export type AttentionKind =
  | 'question'
  | 'permission'
  | 'plan_review'
  | 'document_review'
  | 'agent_exited'
  | 'ci_failed'
  | 'task_blocked';

export interface Attention {
  id: AttentionId;
  kind: AttentionKind;
  agentId: AgentId | null;
  taskId: TaskId | null;
  documentId: DocumentId | null;
  requestId: RequestId | null;
  summary: string;
  raisedAt: string;
  clearedAt: string | null;
}

export function isOpen(item: Attention): boolean {
  return item.clearedAt === null;
}
