import { useQuery } from '@tanstack/react-query';
import type { FileDiff, WorktreeChanges } from '../protocol/entities';
import type { WorktreeId } from '../protocol/ids';
import { useApi } from './ApiProvider';
import { keys } from './keys';

export interface DiffBody {
  rev: string;
  files: FileDiff[];
}

const base = (worktreeId: WorktreeId) => `/api/worktrees/${encodeURIComponent(worktreeId)}`;

/** A worktree's dirty files and the commits its branch has over its base. */
export function useWorktreeChanges(worktreeId: WorktreeId) {
  const api = useApi();
  return useQuery({
    queryKey: keys.worktreeChanges.changes(worktreeId),
    queryFn: () => api.get<WorktreeChanges>(`${base(worktreeId)}/changes`),
  });
}

/**
 * One rev's diff: `uncommitted`, `branch`, or a commit on the branch. Waits
 * while `rev` is `null`, because the default rev depends on the changes.
 */
export function useWorktreeDiff(worktreeId: WorktreeId, rev: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: keys.worktreeChanges.diff(worktreeId, rev ?? ''),
    queryFn: () =>
      api.get<DiffBody>(`${base(worktreeId)}/diff?rev=${encodeURIComponent(rev ?? '')}`),
    enabled: rev !== null,
  });
}
