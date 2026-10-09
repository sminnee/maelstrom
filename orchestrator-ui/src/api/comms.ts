import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { Comm } from '../protocol/entities';
import { useApi } from './ApiProvider';
import { keys } from './keys';

export interface CommsBody {
  comms: Comm[];
}

/** The fields of a comm the UI may write, all optional. Only the keys present are written. */
export interface CommEdit {
  title?: string;
  content?: string;
  recipients?: string[];
  category?: string;
  /** `''` or a project the world holds. */
  project?: string;
  /** `true` closes the comm and stamps `closedAt`; `false` opens it again. */
  closed?: boolean;
}

/** Every comm, open and closed. The view filters in memory. */
export function useComms() {
  const api = useApi();
  return useQuery({
    queryKey: keys.comms.list(),
    queryFn: () => api.get<CommsBody>('/api/comms'),
  });
}

export function useComm(commId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: keys.comms.detail(commId ?? ''),
    queryFn: () => api.get<Comm>(`/api/comms/${commId}`),
    enabled: commId !== null,
  });
}

/** Write a new comm. It links to no task yet: a link is a task edit. */
export function useCreateComm() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: Omit<CommEdit, 'closed'> & { title: string }) =>
      api.post<{ id: string }>('/api/comms', vars),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: keys.comms.list() }),
  });
}

/** Write the given fields of a comm, or close or reopen it. */
export function useUpdateComm() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { commId: string; fields: CommEdit }) =>
      api.patch(`/api/comms/${vars.commId}`, vars.fields),
    onSuccess: (_result, vars) => {
      void queryClient.invalidateQueries({ queryKey: keys.comms.list() });
      void queryClient.invalidateQueries({ queryKey: keys.comms.detail(vars.commId) });
    },
  });
}
