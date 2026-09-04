import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { Task, TaskStatus } from '../protocol/entities';
import type { AgentId, TaskId } from '../protocol/ids';
import { useApi } from './ApiProvider';
import { keys } from './keys';
import type { TaskEdit, TaskRow } from './types';

export interface TasksBody {
  tasks: TaskRow[];
  version: string | null;
}

/** Every task in every project, as slim rows. The views filter in memory. */
export function useTasks() {
  const api = useApi();
  return useQuery({
    queryKey: keys.tasks.list(),
    queryFn: () => api.get<TasksBody>('/api/tasks'),
  });
}

/** One task with its prose. The wire id is `<project>/<notebookId>`, two path segments. */
export function useTask(taskId: TaskId | null) {
  const api = useApi();
  return useQuery({
    queryKey: keys.tasks.detail(taskId ?? ''),
    queryFn: () => api.get<Task>(`/api/tasks/${taskId}`),
    enabled: taskId !== null,
  });
}

/** A launch can open a worktree first, which takes a while: this call waits longer. */
export const LAUNCH_TIMEOUT_MS = 120_000;

function invalidateTask(queryClient: ReturnType<typeof useQueryClient>, taskId: TaskId) {
  void queryClient.invalidateQueries({ queryKey: keys.tasks.list() });
  void queryClient.invalidateQueries({ queryKey: keys.tasks.detail(taskId) });
}

/** Start an agent on a task. The reply follows the host's start, so it can take a while. */
export function useLaunch() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { taskId: TaskId; model?: string }) =>
      api.post<{ agentId: AgentId }>(
        `/api/tasks/${vars.taskId}/launch`,
        vars.model ? { model: vars.model } : {},
        { timeoutMs: LAUNCH_TIMEOUT_MS },
      ),
    onSuccess: (_result, vars) => {
      invalidateTask(queryClient, vars.taskId);
      void queryClient.invalidateQueries({ queryKey: keys.agents.list() });
      void queryClient.invalidateQueries({ queryKey: keys.desk() });
    },
  });
}

export function useSetStatus() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { taskId: TaskId; status: TaskStatus }) =>
      api.post(`/api/tasks/${vars.taskId}/status`, { status: vars.status }),
    onSuccess: (_result, vars) => invalidateTask(queryClient, vars.taskId),
  });
}

/** Write the given fields of a task; an omitted field is left as-is. */
export function useUpdateTask() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { taskId: TaskId; fields: TaskEdit }) =>
      api.patch(`/api/tasks/${vars.taskId}`, vars.fields),
    onSuccess: (_result, vars) => invalidateTask(queryClient, vars.taskId),
  });
}
