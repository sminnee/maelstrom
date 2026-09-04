import { useQuery } from '@tanstack/react-query';
import type { Worktree } from '../protocol/entities';
import { useApi } from './ApiProvider';
import { keys } from './keys';

export interface WorktreesBody {
  worktrees: Worktree[];
}

export function useWorktrees() {
  const api = useApi();
  return useQuery({
    queryKey: keys.worktrees(),
    queryFn: () => api.get<WorktreesBody>('/api/worktrees'),
  });
}
