import { useQuery } from '@tanstack/react-query';
import { useApi } from './ApiProvider';
import { keys } from './keys';

/** Where the browser is, as the server sees it. Not part of the world: it differs per browser. */
export interface ClientBody {
  /** The browser is on the server's machine. */
  local: boolean;
  /** The Remote host the browser is on, or `''` when it is local or on none. */
  host: string;
}

/** Where this browser is. It does not change while the page is open. */
export function useClient() {
  const api = useApi();
  return useQuery({
    queryKey: keys.client(),
    queryFn: ({ signal }) => api.get<ClientBody>('/api/client', { signal }),
    staleTime: Infinity,
  });
}
