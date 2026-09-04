import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { Task, TaskStatus } from '../protocol/entities';
import type { AgentId, TaskId } from '../protocol/ids';
import { useApi } from './ApiProvider';
import { SLOW_CALL_TIMEOUT_MS } from './http';
import { keys } from './keys';
import type { InferredTask, TaskCreate, TaskEdit, TaskRow } from './types';

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
        { timeoutMs: SLOW_CALL_TIMEOUT_MS },
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

/**
 * Name a task from its prose. Slow enough to need the long timeout, and it
 * writes nothing, so nothing is invalidated.
 */
export function useInferTask() {
  const api = useApi();
  return useMutation({
    mutationFn: (vars: { project: string; draft: string }) =>
      api.post<InferredTask>('/api/tasks/infer', vars, { timeoutMs: SLOW_CALL_TIMEOUT_MS }),
  });
}

/**
 * Write a new task, and start it too when `launch` is set. A launch may open
 * a worktree first, so this takes the long timeout either way.
 */
export function useCreateTask() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: TaskCreate) =>
      api.post<{ taskId: TaskId; agentId?: AgentId }>('/api/tasks', vars, {
        timeoutMs: SLOW_CALL_TIMEOUT_MS,
      }),
    onSuccess: (_result, vars) => {
      // The task is new, so there is no detail entry to invalidate — only the
      // list it joins and the desk it is filed on, both ways of saving it.
      void queryClient.invalidateQueries({ queryKey: keys.tasks.list() });
      void queryClient.invalidateQueries({ queryKey: keys.desk() });
      if (!vars.launch) return;
      // The launched agent can be waiting on something already, as a launch's can.
      void queryClient.invalidateQueries({ queryKey: keys.agents.list() });
      void queryClient.invalidateQueries({ queryKey: keys.attention() });
    },
  });
}
