import type { Story } from '@ladle/react';
import { QueryClient } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { ApiProvider } from '../api/ApiProvider';
import type { Worktree } from '../protocol/entities';
import { createFakeServer } from '../fake/fakeServer';
import { makeWorktree, worldWith } from '../fake/fixtures';
import { MergeControl } from './MergeControl';

export default { title: 'Worktrees / MergeControl' };

const ready = makeWorktree({ prNumber: 118, prState: 'ready', base: 'main' });
const refused = makeWorktree({
  id: 'northwind-bravo' as Worktree['id'],
  nato: 'bravo',
  prNumber: 121,
  prState: 'ready',
  base: 'main',
});

/** The control posts through `useMergePullRequest`, so it takes the suite's own fake. */
const server = createFakeServer({ world: worldWith({ worktrees: [ready, refused] }) });
// GitHub refuses a merge whose head moved after the last poll.
server.refuse(/POST \/api\/worktrees\/northwind-bravo\/merge-pr$/, {
  status: 400,
  code: 'invalid',
  message:
    'Failed to merge the pull request: Head branch was modified. Review and try the merge again.',
});

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: Infinity } },
});

function Row({ label, worktree }: { label: string; worktree: Worktree }) {
  return (
    <div style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'center' }}>
      <span style={{ width: '14em', color: 'var(--fg-faint)' }}>{label}</span>
      <MergeControl worktree={worktree} />
    </div>
  );
}

function Board({ children }: { children: ReactNode }) {
  return (
    <ApiProvider api={server.api} queryClient={queryClient}>
      <div
        style={{
          // The question opens leftward from its button, so the rows sit clear of the edge.
          padding: 'var(--space-5) var(--space-5) var(--space-5) 16em',
          fontFamily: 'var(--font)',
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--space-6)',
        }}
      >
        {children}
      </div>
    </ApiProvider>
  );
}

/** A ready PR draws the button. Every other state draws nothing. */
export const States: Story = () => (
  <Board>
    <Row label="ready" worktree={ready} />
    <Row label="ready, GitHub refuses" worktree={refused} />
    <Row label="CI running" worktree={makeWorktree({ prNumber: 119, prState: 'ci-running' })} />
    <Row
      label="draft"
      worktree={makeWorktree({ prNumber: 120, prState: 'ready', prDraft: true })}
    />
    <Row label="no pull request" worktree={makeWorktree()} />
  </Board>
);
