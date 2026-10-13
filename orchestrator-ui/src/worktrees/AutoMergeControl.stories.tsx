import type { Story } from '@ladle/react';
import { QueryClient } from '@tanstack/react-query';
import { ApiProvider } from '../api/ApiProvider';
import type { Worktree } from '../protocol/entities';
import { createFakeServer } from '../fake/fakeServer';
import { makeWorktree, worldWith } from '../fake/fixtures';
import { AutoMergeControl } from './AutoMergeControl';

export default { title: 'Worktrees / AutoMergeControl' };

const waiting = { prNumber: 118, prState: 'ci-running', prAutoMergeAllowed: true } as const;

/** The control posts through `useSetAutoMerge`, so it takes the suite's own fake. */
const server = createFakeServer({ world: worldWith({ worktrees: [makeWorktree(waiting)] }) });

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: Infinity } },
});

function Row({ label, worktree }: { label: string; worktree: Worktree }) {
  return (
    <div style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'center' }}>
      <span style={{ width: '16em', color: 'var(--fg-faint)' }}>{label}</span>
      <AutoMergeControl worktree={worktree} />
    </div>
  );
}

/**
 * A waiting PR draws the switch, off or on, disabled while the local branch
 * differs. A red build keeps it only while it is on.
 */
export const States: Story = () => (
  <ApiProvider api={server.api} queryClient={queryClient}>
    <div
      style={{
        padding: 'var(--space-5)',
        fontFamily: 'var(--font)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--space-5)',
      }}
    >
      <Row label="CI running, off" worktree={makeWorktree(waiting)} />
      <Row label="CI running, on" worktree={makeWorktree({ ...waiting, prAutoMerge: true })} />
      <Row
        label="CI running, local differs"
        worktree={makeWorktree({ ...waiting, prMatch: 'differ' })}
      />
      <Row
        label="CI failed, on"
        worktree={makeWorktree({ ...waiting, prState: 'ci-failed', prAutoMerge: true })}
      />
      <Row label="CI failed, off" worktree={makeWorktree({ ...waiting, prState: 'ci-failed' })} />
      <Row label="ready" worktree={makeWorktree({ ...waiting, prState: 'ready' })} />
      <Row
        label="repo disallows auto-merge"
        worktree={makeWorktree({ ...waiting, prAutoMergeAllowed: false })}
      />
    </div>
  </ApiProvider>
);
