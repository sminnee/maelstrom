import { useState } from 'react';
import { useMergePullRequest } from '../api/worktrees';
import type { Worktree } from '../protocol/entities';
import { ConfirmButton } from '../ui/ConfirmButton';

/**
 * Merges a worktree's pull request. It draws only where the server accepts the
 * merge: **PR state** `ready`, and not a draft.
 */
export function MergeControl({ worktree }: { worktree: Worktree }) {
  const merge = useMergePullRequest();
  const [asking, setAsking] = useState(false);
  if (!worktree.prNumber || worktree.prState !== 'ready' || worktree.prDraft) return null;

  return (
    <ConfirmButton
      variant="primary"
      question={`Merge PR #${worktree.prNumber} into ${worktree.base || 'main'}?`}
      confirm="Merge"
      confirmProcessing="Merging…"
      cancel="Not yet"
      asking={asking}
      onAsk={() => setAsking(true)}
      onDismiss={() => setAsking(false)}
      onConfirm={() => merge.mutateAsync({ worktreeId: worktree.id })}
    >
      Merge
    </ConfirmButton>
  );
}
