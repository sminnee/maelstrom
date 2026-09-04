import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { Agent } from '../protocol/entities';
import type { AgentId, RequestId } from '../protocol/ids';
import type { PermissionRequestItem, PlanReviewItem, QuestionItem } from '../protocol/transcript';
import { useApi } from './ApiProvider';
import { SLOW_CALL_TIMEOUT_MS } from './http';
import { keys } from './keys';
import type { AgentStart } from './types';

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

/** Every agent command changes the agent, its list row and the attention it raised. */
function useAgentMutation<V extends { agentId: AgentId }>(
  send: (api: ReturnType<typeof useApi>, vars: V) => Promise<unknown>,
) {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: V) => send(api, vars),
    onSuccess: (_result, vars) => {
      void queryClient.invalidateQueries({ queryKey: keys.agents.list() });
      void queryClient.invalidateQueries({ queryKey: keys.agents.detail(vars.agentId) });
      void queryClient.invalidateQueries({ queryKey: keys.attention() });
    },
  });
}

export function useApprove() {
  return useAgentMutation((api, vars: { agentId: AgentId; requestId: RequestId }) =>
    api.post(`/api/agents/${vars.agentId}/approve`, { requestId: vars.requestId }),
  );
}

export function useDeny() {
  return useAgentMutation((api, vars: { agentId: AgentId; requestId: RequestId; reason: string }) =>
    api.post(`/api/agents/${vars.agentId}/deny`, {
      requestId: vars.requestId,
      reason: vars.reason,
    }),
  );
}

export function useAnswer() {
  return useAgentMutation(
    (api, vars: { agentId: AgentId; requestId: RequestId; answers: Record<string, string> }) =>
      api.post(`/api/agents/${vars.agentId}/answer`, {
        requestId: vars.requestId,
        answers: vars.answers,
      }),
  );
}

export function useSay() {
  return useAgentMutation((api, vars: { agentId: AgentId; text: string }) =>
    api.post(`/api/agents/${vars.agentId}/say`, { text: vars.text }),
  );
}

/** Change a running agent's permission mode. The child's own status event moves the world. */
export function useSetMode() {
  return useAgentMutation((api, vars: { agentId: AgentId; mode: string }) =>
    api.post(`/api/agents/${vars.agentId}/set-mode`, { mode: vars.mode }),
  );
}

export function useStop() {
  return useAgentMutation((api, vars: { agentId: AgentId }) =>
    api.post(`/api/agents/${vars.agentId}/stop`),
  );
}

export function useResume() {
  return useAgentMutation((api, vars: { agentId: AgentId; text?: string }) =>
    api.post(`/api/agents/${vars.agentId}/resume`, vars.text ? { text: vars.text } : {}),
  );
}

/**
 * Start an agent tied to no task. It may have to open a worktree for the
 * branch first, so it takes the same long timeout a launch does.
 */
export function useStartAgent() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: AgentStart) =>
      api.post<{ agentId: AgentId }>('/api/agents', vars, { timeoutMs: SLOW_CALL_TIMEOUT_MS }),
    onSuccess: () => {
      // A new agent: no detail entry yet, and no attention it could have cleared.
      void queryClient.invalidateQueries({ queryKey: keys.agents.list() });
      void queryClient.invalidateQueries({ queryKey: keys.desk() });
    },
  });
}
