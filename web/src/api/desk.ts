import { useQuery } from '@tanstack/react-query';
import type { DeskEntry } from '../protocol/entities';
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
