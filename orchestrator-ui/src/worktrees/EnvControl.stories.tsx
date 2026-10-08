import type { Story } from '@ladle/react';
import { MemoryNav } from '../nav/MemoryNav';
import { QueryClient } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { ApiProvider } from '../api/ApiProvider';
import type { EnvStateName, Worktree } from '../protocol/entities';
import { createFakeServer } from '../fake/fakeServer';
import { makeWorktree } from '../fake/fixtures';
import { DevEnvLinks } from './DevEnvLinks';
import { EnvControl } from './EnvControl';

export default { title: 'Worktrees / EnvControl' };

/** The control posts through `useEnvWorktree`, so it takes the suite's own fake. */
const { api } = createFakeServer();

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: Infinity } },
});

function worktree(state: EnvStateName, ladleRunning: boolean): Worktree {
  const up = state !== 'stopped';
  return makeWorktree({
    env: {
      state,
      services: [
        { name: 'web', optional: false, running: up, url: 'http://localhost:4210' },
        { name: 'worker', optional: false, running: state === 'running', url: '' },
        { name: 'ladle', optional: true, running: ladleRunning, url: 'http://localhost:4212' },
      ],
    },
  });
}

function Row({ label, worktree }: { label: string; worktree: Worktree }) {
  return (
    <div style={{ display: 'flex', gap: 'var(--u)', alignItems: 'center' }}>
      <span style={{ width: '12em', color: 'var(--fg-faint)' }}>{label}</span>
      <EnvControl worktree={worktree} />
      <DevEnvLinks worktree={worktree} />
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
        <MemoryNav>{children}</MemoryNav>
      </div>
    </ApiProvider>
  );
}

export const States: Story = () => (
  <Board>
    <Row label="stopped, no optional" worktree={makeWorktree()} />
    <Row label="stopped" worktree={worktree('stopped', false)} />
    <Row label="partial" worktree={worktree('partial', false)} />
    <Row label="running" worktree={worktree('running', false)} />
    <Row label="running, ladle up" worktree={worktree('running', true)} />
  </Board>
);
