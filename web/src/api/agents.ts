import { useQuery } from '@tanstack/react-query';
import type { Agent } from '../protocol/entities';
import type { AgentId } from '../protocol/ids';
import type { PermissionRequestItem, PlanReviewItem, QuestionItem } from '../protocol/transcript';
import { useApi } from './ApiProvider';
import { keys } from './keys';

export interface AgentsBody {
  agents: Agent[];
}

/** The item an agent waits on, as its detail carries it. */
export type PendingRequest = QuestionItem | PermissionRequestItem | PlanReviewItem;

export interface AgentDetail extends Agent {
  pendingRequest: PendingRequest | null;
}

export function useAgents() {
  const api = useApi();
  return useQuery({
    queryKey: keys.agents.list(),
    queryFn: () => api.get<AgentsBody>('/api/agents'),
  });
}

/** One agent plus the request it waits on, so a decision renders with no transcript open. */
export function useAgent(agentId: AgentId | null) {
  const api = useApi();
  return useQuery({
    queryKey: keys.agents.detail(agentId ?? ''),
    queryFn: () => api.get<AgentDetail>(`/api/agents/${agentId}`),
    enabled: agentId !== null,
  });
}
