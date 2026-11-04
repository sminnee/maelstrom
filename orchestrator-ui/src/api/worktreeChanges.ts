import {
  type UseQueryResult,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useCallback } from 'react';
import type {
  ChangeComment,
  FileDiff,
  FileExcerpt,
  FileGroup,
  FileList,
  FileVersions,
  LineRange,
  WorktreeChanges,
} from '../protocol/entities';
import type { AgentId } from '../protocol/ids';
import type { WorktreeId } from '../protocol/ids';
import { useApi } from './ApiProvider';
import { keys } from './keys';

export interface DiffBody {
  rev: string;
  /** The files by **Diff group**: config order, then Other. Draw order, not diff order. */
  groups: FileGroup[];
  files: FileDiff[];
}

const base = (worktreeId: WorktreeId) => `/api/worktrees/${encodeURIComponent(worktreeId)}`;

/** A worktree's dirty files and the commits its branch has over its base. */
export function useWorktreeChanges(worktreeId: WorktreeId) {
  const api = useApi();
  return useQuery({
    queryKey: keys.worktreeChanges.changes(worktreeId),
    queryFn: ({ signal }) => api.get<WorktreeChanges>(`${base(worktreeId)}/changes`, { signal }),
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
    queryFn: ({ signal }) =>
      api.get<DiffBody>(`${base(worktreeId)}/diff?rev=${encodeURIComponent(rev ?? '')}`, {
        signal,
      }),
    enabled: rev !== null,
  });
}

/**
 * One file whole, on each side of `rev`, for the Rendered view. `oldPath`
 * names the old side of a rename.
 */
export function useWorktreeFile(
  worktreeId: WorktreeId,
  rev: string,
  path: string,
  oldPath: string | null,
  { enabled = true }: { enabled?: boolean } = {},
) {
  const api = useApi();
  const query = new URLSearchParams({ rev, path });
  if (oldPath) query.set('oldPath', oldPath);
  return useQuery({
    queryKey: keys.worktreeChanges.file(worktreeId, rev, path, oldPath),
    queryFn: ({ signal }) => api.get<FileVersions>(`${base(worktreeId)}/file?${query}`, { signal }),
    enabled,
  });
}

/** Every file path on the new side of `rev`, for the file list's All view. */
export function useWorktreeTree(
  worktreeId: WorktreeId,
  rev: string | null,
  { enabled = true }: { enabled?: boolean } = {},
) {
  const api = useApi();
  return useQuery({
    queryKey: keys.worktreeChanges.tree(worktreeId, rev ?? ''),
    queryFn: ({ signal }) =>
      api.get<FileList>(`${base(worktreeId)}/tree?rev=${encodeURIComponent(rev ?? '')}`, {
        signal,
      }),
    enabled: enabled && rev !== null,
  });
}

/** A range of one file to read: a pin's target. A `null` range is the whole file. */
export interface ExcerptTarget {
  id: string;
  path: string;
  range: LineRange | null;
}

/**
 * Each target's lines on the new side of `rev`, by target id, and the ids
 * whose read failed. A target not read yet is in neither. The answer keeps its
 * identity while `targets` and the reads do, so pass a memoised list.
 */
export function useWorktreeExcerpts(worktreeId: WorktreeId, rev: string, targets: ExcerptTarget[]) {
  const api = useApi();
  const combine = useCallback(
    (results: UseQueryResult<FileExcerpt>[]) => {
      const read = new Map<string, FileExcerpt>();
      const failed = new Set<string>();
      results.forEach((r, i) => {
        const id = targets[i]!.id;
        if (r.data) read.set(id, r.data);
        else if (r.isError) failed.add(id);
      });
      return { read, failed };
    },
    [targets],
  );
  return useQueries({
    queries: targets.map((t) => {
      const query = new URLSearchParams({ rev, path: t.path });
      if (t.range) {
        query.set('start', String(t.range.start));
        query.set('end', String(t.range.end));
      }
      return {
        queryKey: keys.worktreeChanges.excerpt(worktreeId, rev, t.path, t.range),
        queryFn: ({ signal }: { signal: AbortSignal }) =>
          api.get<FileExcerpt>(`${base(worktreeId)}/excerpt?${query}`, { signal }),
      };
    }),
    combine,
  });
}

/**
 * The URL of one side of a file of `rev`, as its raw bytes: an `<img>` src.
 * `oldPath` names the old side of a rename.
 */
export function worktreeFileUrl(
  worktreeId: WorktreeId,
  rev: string,
  path: string,
  side: 'old' | 'new',
  oldPath: string | null = null,
): string {
  const query = new URLSearchParams({ rev, path, side });
  if (oldPath) query.set('oldPath', oldPath);
  return `${base(worktreeId)}/raw?${query}`;
}

/** What a post of change comments answers: who took the message, and who refused it. */
export interface PostedComments {
  agentIds: AgentId[];
  refused: { agentId: AgentId; message: string }[];
}

/**
 * Post change comments: one message to each agent in the worktree. The message
 * is a user turn, so it moves what `useAgentMutation` moves.
 */
export function usePostChangeComments() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { worktreeId: WorktreeId; comments: ChangeComment[] }) =>
      api.post<PostedComments>(`${base(vars.worktreeId)}/comments`, { comments: vars.comments }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.agents.list() });
      void queryClient.invalidateQueries({ queryKey: keys.attention() });
    },
  });
}
