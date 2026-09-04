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
