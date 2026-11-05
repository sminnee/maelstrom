import { useQuery } from '@tanstack/react-query';
import type { Project } from '../protocol/entities';
import { useApi } from './ApiProvider';
import { keys } from './keys';
import { useStartOperation, type OperationStarted } from './operations';

export interface ProjectsBody {
  projects: Project[];
}

export function useProjects() {
  const api = useApi();
  return useQuery({
    queryKey: keys.projects(),
    queryFn: ({ signal }) => api.get<ProjectsBody>('/api/projects', { signal }),
  });
}

/** Clone a git URL as a new project, as `mael project add` does. */
export function useAddProject() {
  return useStartOperation((api, vars: { gitUrl: string }) =>
    api.post<OperationStarted>('/api/projects', vars),
  );
}
