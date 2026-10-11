import { hashKey, type QueryClient } from '@tanstack/react-query';
import type { AgentsBody } from '../api/agents';
import { keys } from '../api/keys';
import type { AgentId } from '../protocol/ids';

/**
 * Calls `onSeen` each time the agent list is fetched and names the agent.
 * Only a fetch after the subscribe counts, so a stale list that still names
 * an agent the server ended does not loop. Returns the unsubscribe.
 */
export function watchAgentList(
  queryClient: QueryClient,
  agentId: AgentId,
  onSeen: () => void,
): () => void {
  const listHash = hashKey(keys.agents.list());
  return queryClient.getQueryCache().subscribe((event) => {
    if (event.type !== 'updated' || event.action.type !== 'success') return;
    if (event.query.queryHash !== listHash) return;
    const body = event.query.state.data as AgentsBody | undefined;
    if (body?.agents.some((agent) => agent.id === agentId)) onSeen();
  });
}
