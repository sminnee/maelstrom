import { useQuery } from '@tanstack/react-query';
import type { GateJob } from '../protocol/entities';
import { useApi } from './ApiProvider';
import { keys } from './keys';

export interface GateJobsBody {
  gateJobs: GateJob[];
}

/** Every queued or running gate job on the machine. A `gateJob` notice refreshes it. */
export function useGateJobs() {
  const api = useApi();
  return useQuery({
    queryKey: keys.gateJobs(),
    queryFn: ({ signal }) => api.get<GateJobsBody>('/api/gate-jobs', { signal }),
  });
}
