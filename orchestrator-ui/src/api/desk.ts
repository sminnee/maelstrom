import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { DeskEntry } from '../protocol/entities';
import type { DeskId, WorktreeId } from '../protocol/ids';
import { useApi } from './ApiProvider';
import { keys } from './keys';
import { runOperation, type OperationStarted } from './operations';

export interface DeskBody {
  desk: DeskEntry[];
}

export function useDesk() {
  const api = useApi();
  return useQuery({
    queryKey: keys.desk(),
    queryFn: ({ signal }) => api.get<DeskBody>('/api/desk', { signal }),
  });
}

export function usePutOnDesk() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: DeskId }) => api.post('/api/desk', { id: vars.id }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: keys.desk() }),
  });
}

/** The desk id holds a `/`, so it travels URL-encoded. */
export function useTakeOffDesk() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: DeskId }) => api.delete(`/api/desk/${encodeURIComponent(vars.id)}`),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: keys.desk() }),
  });
}

/** What a **Dismiss** runs before the node leaves the desk. See `CONTEXT.md`, "Dismiss". */
export type DismissChain = 'terminate' | 'close' | 'shelve' | 'trash' | 'delete';

/**
 * **Dismiss** a node: the server stops its agent or ends its worktree, then
 * takes it off the desk, as one operation. It waits for the operation to end,
 * so the control that ran it shows the wait and the reason. A worktree chain
 * names the worktree it ends.
 */
export function useDismiss() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { deskId: DeskId; chain: DismissChain; worktreeId?: WorktreeId }) =>
      runOperation(queryClient, api, () =>
        api.post<OperationStarted>(`/api/desk/${encodeURIComponent(vars.deskId)}/dismiss`, {
          chain: vars.chain,
          ...(vars.worktreeId ? { worktreeId: vars.worktreeId } : {}),
        }),
      ),
    onSettled: () => {
      // A refused end has still stopped agents, so the world is read either way.
      void queryClient.invalidateQueries({ queryKey: keys.worktrees() });
      void queryClient.invalidateQueries({ queryKey: keys.agents.list() });
      void queryClient.invalidateQueries({ queryKey: keys.attention() });
      void queryClient.invalidateQueries({ queryKey: keys.desk() });
    },
  });
}
