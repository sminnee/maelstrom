import { describe, expect, it } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ApiProvider } from '../api/ApiProvider';
import { createFakeServer } from '../fake/fakeServer';
import { makeMergeRequest, makeWorktree, worldWith } from '../fake/fixtures';
import type { Worktree, WorktreeMergeRequest } from '../protocol/entities';
import { MergeControl } from './MergeControl';

const SHA = 'b'.repeat(40);

function renderControl(over: Partial<WorktreeMergeRequest>, worktreeOver: Partial<Worktree> = {}) {
  const worktree = makeWorktree({
    ...worktreeOver,
    mergeRequest: makeMergeRequest({ sha: SHA, ...over }),
  });
  const server = createFakeServer({ world: worldWith({ worktrees: [worktree] }) });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <ApiProvider api={server.api} queryClient={queryClient}>
      <MergeControl worktree={worktree} />
    </ApiProvider>,
  );
  return server;
}

const posts = (server: ReturnType<typeof createFakeServer>) =>
  server.requests.filter((r) => r.method === 'POST' && r.path.includes('/merge-request/'));

describe('MergeControl with a Merge request', () => {
  it.each<[WorktreeMergeRequest['status'], string]>([
    ['passed', 'Merge'],
    ['stale', 'Rebase and rebuild'],
    ['failed', 'Rebuild'],
  ])('draws the %s action', (status, label) => {
    renderControl({ status });
    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual([label]);
  });

  it.each(['queued', 'building', 'withdrawn', 'merged'] as const)(
    'draws nothing while %s',
    (status) => {
      renderControl({ status });
      expect(screen.queryByRole('button')).toBeNull();
    },
  );

  it('draws the Merge request, not the pull request, when the branch has both', () => {
    renderControl({ status: 'building' }, { prNumber: 118, prState: 'ready' });
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('shows the description and posts the pinned SHA on confirm', async () => {
    const server = renderControl({ status: 'passed', description: '## Why\n\nFaster builds.' });
    await userEvent.click(screen.getByRole('button', { name: 'Merge' }));
    const ask = screen.getByRole('alertdialog', { name: 'Merge mr3 at bbbbbbb into main?' });
    expect(ask).toHaveTextContent('Faster builds.');
    expect(screen.getByRole('heading', { name: 'Why' })).toBeInTheDocument();
    const [, confirm] = screen.getAllByRole('button', { name: 'Merge' });
    await userEvent.click(confirm!);
    await waitFor(() => expect(posts(server)).toHaveLength(1));
    expect(posts(server)[0]).toMatchObject({
      path: '/api/tasks/northwind/t1/merge-request/merge',
      body: { sha: SHA },
    });
  });

  it.each<[WorktreeMergeRequest['status'], string, string]>([
    ['stale', 'Rebase and rebuild', 'rebase'],
    ['failed', 'Rebuild', 'retry'],
  ])('%s posts %s at once', async (status, label, action) => {
    const server = renderControl({ status });
    await userEvent.click(screen.getByRole('button', { name: label }));
    await waitFor(() => expect(posts(server)).toHaveLength(1));
    expect(posts(server)[0]!.path).toBe(`/api/tasks/northwind/t1/merge-request/${action}`);
  });
});
