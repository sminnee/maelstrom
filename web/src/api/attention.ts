import { useQuery } from '@tanstack/react-query';
import type { Attention } from '../protocol/attention';
import { useApi } from './ApiProvider';
import { keys } from './keys';

export interface AttentionBody {
  attention: Attention[];
}

/** Every attention item, cleared ones included: the selectors decide what is open. */
export function useAttention() {
  const api = useApi();
  return useQuery({
    queryKey: keys.attention(),
    queryFn: () => api.get<AttentionBody>('/api/attention'),
  });
}
