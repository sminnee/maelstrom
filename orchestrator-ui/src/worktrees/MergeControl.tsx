import { useState } from 'react';
import { useMergeMergeRequest, useRebaseMergeRequest, useRetryMergeRequest } from '../api/tasks';
import { useMergePullRequest } from '../api/worktrees';
import { Markdown } from '../markdown/Markdown';
import type { Worktree, WorktreeMergeRequest } from '../protocol/entities';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { ConfirmButton } from '../ui/ConfirmButton';

/**
 * Merges a worktree's work. With a **Merge request** (`merge: local`), it acts
 * on the request; otherwise on the pull request.
 *
 * A pull request draws the button only on **PR state** `ready`, not a draft,
 * and draws it disabled while the **PR match** is `differ`.
 */
export function MergeControl({ worktree }: { worktree: Worktree }) {
  if (worktree.mergeRequest) return <MergeRequestControl mr={worktree.mergeRequest} />;
  return <PullRequestMerge worktree={worktree} />;
}

function PullRequestMerge({ worktree }: { worktree: Worktree }) {
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
      question={`Merge PR #${worktree.prNumber} into main?`}
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

/**
 * The Merge request's next action: Merge when `passed`, "Rebase and rebuild"
 * when `stale`, "Rebuild" when `failed`. Any other status draws nothing.
 *
 * The Merge posts the SHA drawn here; the server refuses any other.
 */
function MergeRequestControl({ mr }: { mr: WorktreeMergeRequest }) {
  const merge = useMergeMergeRequest();
  const rebase = useRebaseMergeRequest();
  const retry = useRetryMergeRequest();
  const [asking, setAsking] = useState(false);
  const short = mr.sha.slice(0, 7);

  if (mr.status === 'passed') {
    return (
      <ConfirmButton
        variant="primary"
        icon={actionIcon('merge')}
        question={`Merge ${mr.id} at ${short} into main?`}
        detail={mr.description ? <Markdown source={mr.description} /> : undefined}
        confirm="Merge"
        confirmProcessing="Merging…"
        cancel="Not yet"
        asking={asking}
        onAsk={() => setAsking(true)}
        onDismiss={() => setAsking(false)}
        onConfirm={() => merge.mutateAsync({ taskId: mr.task, sha: mr.sha })}
      >
        Merge
      </ConfirmButton>
    );
  }
  if (mr.status === 'stale') {
    return (
      <AppButton
        icon={actionIcon('sync')}
        title="main moved since the submit. The agent rebases and submits again."
        onClick={() => rebase.mutateAsync({ taskId: mr.task })}
      >
        Rebase and rebuild
      </AppButton>
    );
  }
  if (mr.status === 'failed') {
    return (
      <AppButton
        icon={actionIcon('retry')}
        title={`Run the gates again on ${short}`}
        onClick={() => retry.mutateAsync({ taskId: mr.task })}
      >
        Rebuild
      </AppButton>
    );
  }
  return null;
}
