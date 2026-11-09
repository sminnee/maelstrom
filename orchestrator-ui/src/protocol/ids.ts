// Ids reuse the repo's own shapes: a project name, `<project>-<nato>` for a
// worktree, a dotted task id, and the daemon's agent id.
export type ProjectId = string;
export type WorktreeId = string;
export type TaskId = string;
export type AgentId = string;
export type DocumentId = string;
export type CommentId = string;
export type AttentionId = string;
/** `task:<task id>` or `agent:<agent id>`. `protocol/deskId` builds and splits one. */
export type DeskId = string;
export type RequestId = string;
export type TranscriptItemId = string;
/** `op<n>`, minted by the server in start order. */
export type OperationId = string;

/**
 * The host that owns an agent, worktree or gate job id: its **Id prefix**, or
 * `''` for a bare id. Mirrors `mael_agent.host_ids.owner_of`.
 */
export function ownerOf(id: string): string {
  const sep = id.indexOf(':');
  const host = sep < 0 ? '' : id.slice(0, sep);
  return /^[a-z0-9][a-z0-9-]*$/.test(host) ? host : '';
}
