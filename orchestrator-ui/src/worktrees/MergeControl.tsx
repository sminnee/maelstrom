import { useState } from 'react';
import { useMergePullRequest } from '../api/worktrees';
import type { Worktree } from '../protocol/entities';
import { actionIcon } from '../ui/actionIcons';
import { ConfirmButton } from '../ui/ConfirmButton';

/**
 * Merges a worktree's pull request. It draws only on **PR state** `ready`, not
 * a draft, and draws disabled while the **PR match** is `differ`.
 */
export function MergeControl({ worktree }: { worktree: Worktree }) {
  const merge = useMergePullRequest();
  const [asking, setAsking] = useState(false);
  if (!worktree.prNumber || worktree.prState !== 'ready' || worktree.prDraft) return null;
  const differs = worktree.prMatch === 'differ';

  return (
    <ConfirmButton
      variant="primary"
      disabled={differs}
      title={differs ? 'The local branch differs from the PR. Sync it first.' : undefined}
      icon={actionIcon('merge')}
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
