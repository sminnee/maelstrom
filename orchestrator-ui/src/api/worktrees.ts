import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { Worktree } from '../protocol/entities';
import type { WorktreeId } from '../protocol/ids';
import { useApi } from './ApiProvider';
import { SLOW_CALL_TIMEOUT_MS } from './http';
import { keys } from './keys';
import { useStartOperation, type OperationStarted } from './operations';

export interface WorktreesBody {
  worktrees: Worktree[];
}

export function useWorktrees() {
  const api = useApi();
  return useQuery({
    queryKey: keys.worktrees(),
    queryFn: ({ signal }) => api.get<WorktreesBody>('/api/worktrees', { signal }),
  });
}

const worktreeUrl = (worktreeId: WorktreeId, action = '') =>
  `/api/worktrees/${encodeURIComponent(worktreeId)}${action && `/${action}`}`;

/** Close a worktree: the same close `mael close` runs, as an operation. See `useStartOperation`. */
export function useCloseWorktree() {
  return useStartOperation((api, vars: { worktreeId: WorktreeId }) =>
    api.post<OperationStarted>(worktreeUrl(vars.worktreeId, 'close')),
  );
}

/**
 * Close a worktree without the checks the ordinary close makes. It is its own
 * call because the checks are the point of the other one: a dirty tree or an
 * unmerged commit stops a close, and this is how a user says to close anyway.
 */
export function useForceCloseWorktree() {
  return useStartOperation((api, vars: { worktreeId: WorktreeId }) =>
    api.post<OperationStarted>(worktreeUrl(vars.worktreeId, 'force-close')),
  );
}

/** Trash a worktree, as an operation. */
export function useTrashWorktree() {
  return useStartOperation((api, vars: { worktreeId: WorktreeId }) =>
    api.post<OperationStarted>(worktreeUrl(vars.worktreeId, 'trash')),
  );
}

/**
 * Remove a worktree, as an operation: the folder and the record both go.
 */
export function useRemoveWorktree() {
  return useStartOperation((api, vars: { worktreeId: WorktreeId }) =>
    api.delete<OperationStarted>(worktreeUrl(vars.worktreeId)),
  );
}

export type SyncMode = 'plain' | 'autorepair' | 'squash';

/** Sync a worktree: the same `mael sync` runs, in one of its three modes, as an operation. */
export function useSyncWorktree() {
  return useStartOperation((api, vars: { worktreeId: WorktreeId; mode: SyncMode }) =>
    api.post<OperationStarted>(worktreeUrl(vars.worktreeId, 'sync'), { mode: vars.mode }),
  );
}

/** Merge a worktree's pull request, as an operation. */
export function useMergePullRequest() {
  return useStartOperation((api, vars: { worktreeId: WorktreeId }) =>
    api.post<OperationStarted>(worktreeUrl(vars.worktreeId, 'merge-pr')),
  );
}

/** Turn **Auto-merge** on or off for a worktree's pull request, as an operation. */
export function useSetAutoMerge() {
  return useStartOperation((api, vars: { worktreeId: WorktreeId; on: boolean }) =>
    api.post<OperationStarted>(worktreeUrl(vars.worktreeId, 'auto-merge'), { on: vars.on }),
  );
}

/**
 * Start, stop or restart a worktree's dev environment, or start or stop one
 * optional `service`, as an operation.
 */
export function useEnvWorktree() {
  return useStartOperation(
    (
      api,
      vars: { worktreeId: WorktreeId; action: 'start' | 'stop' | 'restart'; service?: string },
    ) =>
      api.post<OperationStarted>(worktreeUrl(vars.worktreeId, 'env'), {
        action: vars.action,
        service: vars.service,
      }),
  );
}

/**
 * Re-read the world from the host. Nothing changes but what the server knows,
 * so only the worktrees are re-fetched.
 */
export function useRefreshWorktrees() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.post('/api/worktrees/refresh', undefined, { timeoutMs: SLOW_CALL_TIMEOUT_MS }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.worktrees() });
    },
  });
}

/**
 * Make a worktree's terminal in cmux, and return the link to its pane. The reply
 * is written into the cached worktree, so the control turns into a link
 * without waiting for the pushed world.
 */
export function useCreateWorktreeTerminal() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { worktreeId: WorktreeId }) =>
      api.post<{ shellUrl: string }>(worktreeUrl(vars.worktreeId, 'terminal'), undefined, {
        timeoutMs: SLOW_CALL_TIMEOUT_MS,
      }),
    onSuccess: ({ shellUrl }, { worktreeId }) => {
      queryClient.setQueryData<WorktreesBody>(keys.worktrees(), (body) =>
        body
          ? {
              worktrees: body.worktrees.map((w) => (w.id === worktreeId ? { ...w, shellUrl } : w)),
            }
          : body,
      );
    },
  });
}
