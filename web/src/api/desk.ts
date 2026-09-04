import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { DeskEntry } from '../protocol/entities';
import type { DeskId } from '../protocol/ids';
import { useApi } from './ApiProvider';
import { keys } from './keys';

export interface DeskBody {
  desk: DeskEntry[];
}

export function useDesk() {
  const api = useApi();
  return useQuery({
    queryKey: keys.desk(),
    queryFn: () => api.get<DeskBody>('/api/desk'),
  });
}

export function useAddToDesk() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: DeskId }) => api.post('/api/desk', { id: vars.id }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: keys.desk() }),
  });
}

/** The desk id holds a `/`, so it travels URL-encoded. */
export function useRemoveFromDesk() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: DeskId }) => api.delete(`/api/desk/${encodeURIComponent(vars.id)}`),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: keys.desk() }),
  });
}
