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
    queryFn: ({ signal }) => api.get<HostBody>('/api/host', { signal }),
  });
}

/** This server's name and **Id prefix**, `''` each until the host loads. */
export function useOwnHost(): { name: string; idPrefix: string } {
  const host = useHost().data?.host;
  return { name: host?.name ?? '', idPrefix: host?.idPrefix ?? '' };
}
