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
      // waited on. It leaves the desk as it was.
      void queryClient.invalidateQueries({ queryKey: keys.worktrees() });
      void queryClient.invalidateQueries({ queryKey: keys.agents.list() });
      void queryClient.invalidateQueries({ queryKey: keys.attention() });
      void queryClient.invalidateQueries({ queryKey: keys.desk() });
    },
  });
}

/**
 * Close a worktree without the checks the ordinary close makes. It is its own
 * call because the checks are the point of the other one: a dirty tree or an
 * unmerged commit stops a close, and this is how a user says to close anyway.
 * It tears down the same things, so it clears the same queries.
 */
export function useForceCloseWorktree() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { worktreeId: WorktreeId }) =>
      api.post(`/api/worktrees/${encodeURIComponent(vars.worktreeId)}/force-close`, undefined, {
        timeoutMs: SLOW_CALL_TIMEOUT_MS,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.worktrees() });
      void queryClient.invalidateQueries({ queryKey: keys.agents.list() });
      void queryClient.invalidateQueries({ queryKey: keys.attention() });
      void queryClient.invalidateQueries({ queryKey: keys.desk() });
    },
  });
}

/**
 * Trash a worktree. It tears down what a close does, so it clears the same
 * queries.
 */
export function useTrashWorktree() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { worktreeId: WorktreeId }) =>
      api.post(`/api/worktrees/${encodeURIComponent(vars.worktreeId)}/trash`, undefined, {
        timeoutMs: SLOW_CALL_TIMEOUT_MS,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.worktrees() });
      void queryClient.invalidateQueries({ queryKey: keys.agents.list() });
      void queryClient.invalidateQueries({ queryKey: keys.attention() });
      void queryClient.invalidateQueries({ queryKey: keys.desk() });
    },
  });
}

/**
 * Remove a worktree: the folder and the record both go. It stops whatever is
 * still there first, so it clears what a close clears.
 */
export function useRemoveWorktree() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { worktreeId: WorktreeId }) =>
      api.delete(`/api/worktrees/${encodeURIComponent(vars.worktreeId)}`, {
        timeoutMs: SLOW_CALL_TIMEOUT_MS,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.worktrees() });
      void queryClient.invalidateQueries({ queryKey: keys.agents.list() });
      void queryClient.invalidateQueries({ queryKey: keys.attention() });
      void queryClient.invalidateQueries({ queryKey: keys.desk() });
    },
  });
}

export type SyncMode = 'plain' | 'autorepair' | 'squash';

/**
 * Sync a worktree: the same `mael sync` runs, in one of its three modes. It
 * rebases and pushes, so it takes the long timeout, but it starts and stops no
 * agent — only the worktree's own counts move.
 */
export function useSyncWorktree() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { worktreeId: WorktreeId; mode: SyncMode }) =>
      api.post(
        `/api/worktrees/${encodeURIComponent(vars.worktreeId)}/sync`,
        { mode: vars.mode },
        { timeoutMs: SLOW_CALL_TIMEOUT_MS },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.worktrees() });
    },
  });
}

/**
 * Merge a worktree's pull request. The server waits on GitHub, so it takes the
 * long timeout. Only the worktree's own PR state moves.
 */
export function useMergePullRequest() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { worktreeId: WorktreeId }) =>
      api.post(`/api/worktrees/${encodeURIComponent(vars.worktreeId)}/merge-pr`, undefined, {
        timeoutMs: SLOW_CALL_TIMEOUT_MS,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.worktrees() });
    },
  });
}

/**
 * Start, stop or restart a worktree's dev environment, or start or stop one
 * optional `service`. Only the worktree's own `env` moves, so nothing else is
 * cleared.
 */
export function useEnvWorktree() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: {
      worktreeId: WorktreeId;
      action: 'start' | 'stop' | 'restart';
      service?: string;
    }) =>
      api.post(
        `/api/worktrees/${encodeURIComponent(vars.worktreeId)}/env`,
        { action: vars.action, service: vars.service },
        { timeoutMs: SLOW_CALL_TIMEOUT_MS },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.worktrees() });
    },
  });
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
      api.post<{ shellUrl: string }>(
        `/api/worktrees/${encodeURIComponent(vars.worktreeId)}/terminal`,
        undefined,
        { timeoutMs: SLOW_CALL_TIMEOUT_MS },
      ),
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
