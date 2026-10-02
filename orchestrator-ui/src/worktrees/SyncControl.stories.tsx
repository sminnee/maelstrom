import type { Story } from '@ladle/react';
import { QueryClient } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { ApiProvider } from '../api/ApiProvider';
import type { Worktree } from '../protocol/entities';
import { createFakeServer } from '../fake/fakeServer';
import { makeWorktree } from '../fake/fixtures';
import { SyncControl } from './SyncControl';

export default { title: 'Worktrees / SyncControl' };

/** The control posts through `useSyncWorktree`, so it takes the suite's own fake. */
const { api } = createFakeServer();

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: Infinity } },
});

function Row({ label, worktree }: { label: string; worktree: Worktree }) {
  return (
    <div style={{ display: 'flex', gap: 'var(--u)', alignItems: 'center' }}>
      <span style={{ width: '12em', color: 'var(--fg-faint)' }}>{label}</span>
      <SyncControl worktree={worktree} />
    </div>
  );
}

function Board({ children }: { children: ReactNode }) {
  return (
    <ApiProvider api={api} queryClient={queryClient}>
      <div
        style={{
          padding: 'var(--u-3)',
          fontFamily: 'var(--font)',
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--u-2)',
        }}
      >
        {children}
      </div>
    </ApiProvider>
  );
}

export const Worktrees: Story = () => (
  <Board>
    <Row label="feature worktree" worktree={makeWorktree()} />
    <Row label="_main" worktree={makeWorktree({ nato: '_main', branch: 'main' })} />
  </Board>
);
