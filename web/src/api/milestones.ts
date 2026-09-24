import { useQuery } from '@tanstack/react-query';
import type { AgentId } from '../protocol/ids';
import { useApi } from './ApiProvider';
import { keys } from './keys';

/**
 * One stage an agent reached, and what that stage cost.
 *
 * Snake case because the route serves `mael_domain.agent_cost.Stage` whole —
 * the same report `mael agent cost` prints. Renaming the fields on the way
 * through would give the page and the terminal two vocabularies for one
 * reading.
 */
export interface Stage {
  name: string;
  at: string;
  /** Whether the name is one the flow declares; a typo shows rather than hides. */
  recognised: boolean;
  total_tokens: number;
  /** Own + subagent tokens this stage alone consumed. */
  delta_tokens: number;
  own_delta: number;
  subagent_delta: number;
  cost_delta: number;
}

/** One agent's spend, and the stages it passed through. */
export interface AgentCost {
  agent_id: string;
  own_tokens: number;
  subagent_tokens: number;
  total_tokens: number;
  cost_usd: number;
  cost_is_parent_only: boolean;
  /** In the order the stages were reached. Empty for most agents. */
  stages: Stage[];
}

/**
 * The stages an agent has reached.
 *
 * Read from the ledger rather than scanned out of the transcript: a restarted
 * server keeps the ledger and drops the transcript, and the host's window
 * rolls the older bars away in a long run. The card must still report.
 */
export function useMilestones(agentId: AgentId | null) {
  const api = useApi();
  return useQuery({
    queryKey: keys.milestones(agentId ?? ''),
    queryFn: () => api.get<AgentCost>(`/api/agents/${agentId}/milestones`),
    enabled: agentId !== null,
  });
}
