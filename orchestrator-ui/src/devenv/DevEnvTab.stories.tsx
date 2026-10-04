import type { Story } from '@ladle/react';
import { QueryClient } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { ApiProvider } from '../api/ApiProvider';
import { createFakeServer } from '../fake/fakeServer';
import { makeWorktree } from '../fake/fixtures';
import { DevEnvView } from './DevEnvTab';

export default { title: 'Dev env / DevEnvTab' };

/** The env control posts through `useEnvWorktree`, so it takes the suite's own fake. */
const { api } = createFakeServer();

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: Infinity } },
});

/** `running` frames a page of its own, so the story needs no server behind the frame. */
const worktree = (running: boolean) =>
  makeWorktree({
    nato: 'delta',
    env: {
      state: running ? 'running' : 'stopped',
      services: [
        {
          name: 'web',
          optional: false,
          running,
          url: 'data:text/html,<h1 style="font-family:sans-serif">The framed app</h1>',
        },
      ],
    },
  });

function Frame({ children }: { children: ReactNode }) {
  return (
    <ApiProvider api={api} queryClient={queryClient}>
      <div style={{ height: '24rem', border: '1px solid var(--border)' }}>{children}</div>
    </ApiProvider>
  );
}

export const Running: Story = () => (
  <Frame>
    <DevEnvView worktree={worktree(true)} service="web" />
  </Frame>
);

export const Stopped: Story = () => (
  <Frame>
    <DevEnvView worktree={worktree(false)} service="web" />
  </Frame>
);
