import { useQuery } from '@tanstack/react-query';
import type { Task } from '../protocol/entities';
import type { TaskId } from '../protocol/ids';
import { useApi } from './ApiProvider';
import { keys } from './keys';
import type { TaskRow } from './types';

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
