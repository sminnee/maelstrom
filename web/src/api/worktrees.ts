import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { Worktree } from '../protocol/entities';
import type { WorktreeId } from '../protocol/ids';
import { useApi } from './ApiProvider';
import { SLOW_CALL_TIMEOUT_MS } from './http';
import { keys } from './keys';

export interface WorktreesBody {
  worktrees: Worktree[];
}

export function useWorktrees() {
  const api = useApi();
  return useQuery({
    queryKey: keys.worktrees(),
    queryFn: () => api.get<WorktreesBody>('/api/worktrees'),
  });
}

/**
 * Close a worktree: the same close `mael close` runs. It syncs the branch and
 * stops what lives there, so it takes the long timeout a launch takes.
 */
export function useCloseWorktree() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { worktreeId: WorktreeId }) =>
      api.post(`/api/worktrees/${encodeURIComponent(vars.worktreeId)}/close`, undefined, {
        timeoutMs: SLOW_CALL_TIMEOUT_MS,
      }),
    onSuccess: () => {
      // The close stops every agent in the worktree, which clears what they
      // waited on and empties the desk entries a free agent held.
      void queryClient.invalidateQueries({ queryKey: keys.worktrees() });
      void queryClient.invalidateQueries({ queryKey: keys.agents.list() });
      void queryClient.invalidateQueries({ queryKey: keys.attention() });
      void queryClient.invalidateQueries({ queryKey: keys.desk() });
    },
  });
}
