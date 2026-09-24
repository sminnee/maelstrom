import { useQuery } from '@tanstack/react-query';
import type { Project } from '../protocol/entities';
import { useApi } from './ApiProvider';
import { keys } from './keys';

export interface ProjectsBody {
  projects: Project[];
}

export function useProjects() {
  const api = useApi();
  return useQuery({
    queryKey: keys.projects(),
    queryFn: () => api.get<ProjectsBody>('/api/projects'),
  });
}
