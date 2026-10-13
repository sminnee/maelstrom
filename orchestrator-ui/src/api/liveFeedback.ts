import { useMutation } from '@tanstack/react-query';
import type { WorktreeId } from '../protocol/ids';
import { useApi } from './ApiProvider';

/**
 * Set **Live feedback params** on one dev server of the worktree. The server
 * tells the agents, and the world's change notice moves the switch.
 */
export function useSetFeedbackParams(worktreeId: WorktreeId, service: string) {
  const api = useApi();
  return useMutation({
    mutationFn: (params: Record<string, boolean>) =>
      api.post<Record<string, never>>(
        `/api/worktrees/${encodeURIComponent(worktreeId)}/live-feedback/${encodeURIComponent(service)}/params`,
        { params },
      ),
  });
}
