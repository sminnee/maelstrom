import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { AgentId, TaskId } from '../protocol/ids';
import { useApi } from './ApiProvider';
import { SLOW_CALL_TIMEOUT_MS } from './http';
import { keys } from './keys';

/** One issue of a project's current Linear cycle. */
export interface LinearIssue {
  id: string;
  title: string;
  status: string;
}

/**
 * A project's Linear issues for the current cycle.
 *
 * Only `LinearFields` calls this, and only while the Linear kind is showing, so
 * a session that never plans a Linear issue spends no Linear call. Reaching the
 * Linear API is slow, so this is a slow call like the other two.
 */
export function useLinearIssues(project: string) {
  const api = useApi();
  return useQuery({
    queryKey: keys.linear.issues(project),
    queryFn: () =>
      api.get<{ issues: LinearIssue[] }>(
        `/api/linear/issues?project=${encodeURIComponent(project)}`,
        { timeoutMs: SLOW_CALL_TIMEOUT_MS },
      ),
    enabled: project !== '',
  });
}

/**
 * Plan a Linear issue: the equivalent of `mael linear plan`.
 *
 * Writes the planning task and, with `launch`, starts it — so it invalidates
 * exactly what {@link useCreateTask} does.
 */
export function useCreateLinearTask() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { project: string; issueId: string; launch?: boolean }) =>
      api.post<{ taskId: TaskId; agentId?: AgentId }>('/api/linear/tasks', vars, {
        timeoutMs: SLOW_CALL_TIMEOUT_MS,
      }),
    onSuccess: (_result, vars) => {
      void queryClient.invalidateQueries({ queryKey: keys.tasks.list() });
      void queryClient.invalidateQueries({ queryKey: keys.desk() });
      if (!vars.launch) return;
      void queryClient.invalidateQueries({ queryKey: keys.agents.list() });
      void queryClient.invalidateQueries({ queryKey: keys.attention() });
    },
  });
}
