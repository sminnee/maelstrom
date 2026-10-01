import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ChangeComment, FileDiff, WorktreeChanges } from '../protocol/entities';
import type { AgentId } from '../protocol/ids';
import type { WorktreeId } from '../protocol/ids';
import { useApi } from './ApiProvider';
import { keys } from './keys';

export interface DiffBody {
  rev: string;
  files: FileDiff[];
}

const base = (worktreeId: WorktreeId) => `/api/worktrees/${encodeURIComponent(worktreeId)}`;

/** A worktree's dirty files and the commits its branch has over its base. */
export function useWorktreeChanges(worktreeId: WorktreeId) {
  const api = useApi();
  return useQuery({
    queryKey: keys.worktreeChanges.changes(worktreeId),
    queryFn: () => api.get<WorktreeChanges>(`${base(worktreeId)}/changes`),
  });
}

/**
 * One rev's diff: `uncommitted`, `branch`, or a commit on the branch. Waits
 * while `rev` is `null`, because the default rev depends on the changes.
 */
export function useWorktreeDiff(worktreeId: WorktreeId, rev: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: keys.worktreeChanges.diff(worktreeId, rev ?? ''),
    queryFn: () =>
      api.get<DiffBody>(`${base(worktreeId)}/diff?rev=${encodeURIComponent(rev ?? '')}`),
    enabled: rev !== null,
  });
}

/** What a post of change comments answers: who took the message, and who refused it. */
export interface PostedComments {
  agentIds: AgentId[];
  refused: { agentId: AgentId; message: string }[];
}

/**
 * Post change comments: one message to each agent in the worktree. The message
 * is a user turn, so it moves what `useAgentMutation` moves.
 */
export function usePostChangeComments() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { worktreeId: WorktreeId; comments: ChangeComment[] }) =>
      api.post<PostedComments>(`${base(vars.worktreeId)}/comments`, { comments: vars.comments }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.agents.list() });
      void queryClient.invalidateQueries({ queryKey: keys.attention() });
    },
  });
}
