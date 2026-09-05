import { useQuery } from '@tanstack/react-query';
import type { Host } from '../protocol/entities';
import { useApi } from './ApiProvider';
import { keys } from './keys';

export interface HostBody {
  /** `null` until the server's first agent poll has settled. */
  host: Host | null;
}

/** Whether the agent host answers. Not part of the world: nothing draws from it but the banner. */
export function useHost() {
  const api = useApi();
  return useQuery({
    queryKey: keys.host(),
    queryFn: () => api.get<HostBody>('/api/host'),
  });
}
