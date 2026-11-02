import { describe, expect, it } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ApiProvider } from '../api/ApiProvider';
import { useWorktrees } from '../api/worktrees';
import { createFakeServer } from '../fake/fakeServer';
import { makeWorktree, worldWith } from '../fake/fixtures';
import type { Worktree } from '../protocol/entities';
import { AutoMergeControl } from './AutoMergeControl';

const waiting = { prNumber: 118, prState: 'ci-running', prAutoMergeAllowed: true } as const;

/** The control over the row the worktrees query holds, as the app draws it. */
function LiveControl({ fallback }: { fallback: Worktree }) {
  const worktrees = useWorktrees();
  const worktree = worktrees.data?.worktrees.find((w) => w.id === fallback.id) ?? fallback;
  return <AutoMergeControl worktree={worktree} />;
}

function renderControl(over: Partial<Worktree>) {
  const worktree = makeWorktree({ ...waiting, ...over });
  const server = createFakeServer({ world: worldWith({ worktrees: [worktree] }) });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <ApiProvider api={server.api} queryClient={queryClient}>
      <LiveControl fallback={worktree} />
    </ApiProvider>,
  );
  return server;
}

const toggle = () => screen.queryByRole('switch', { name: 'Auto-merge' });

describe('AutoMergeControl', () => {
  it.each(['ci-running', 'unknown', 'checks-unreadable'] as const)(
    'draws unchecked while the PR waits (%s)',
    (prState) => {
      renderControl({ prState });
      expect(toggle()).toHaveAttribute('aria-checked', 'false');
    },
  );

  it.each<[string, Partial<Worktree>]>([
    ['a ready PR, which merges now instead', { prState: 'ready' }],
    ['a red build', { prState: 'ci-failed' }],
    ['a conflict', { prState: 'conflict' }],
    ['a merged PR', { prState: 'merged' }],
    ['a merged PR, though auto-merge reads on', { prState: 'merged', prAutoMerge: true }],
    ['a draft', { prDraft: true }],
    ['a repo that does not allow auto-merge', { prAutoMergeAllowed: false }],
    ['no PR', { prNumber: null, prState: '' }],
  ])('draws nothing for %s', (_label, over) => {
    renderControl(over);
    expect(toggle()).toBeNull();
  });

  it.each(['ci-failed', 'conflict', 'ready'] as const)(
    'stays drawn when on, so %s can turn it off',
    (prState) => {
      renderControl({ prState, prAutoMerge: true });
      expect(toggle()).toHaveAttribute('aria-checked', 'true');
    },
  );

  it('cannot turn on while the local branch differs', () => {
    renderControl({ prMatch: 'differ' });
    expect(toggle()).toBeDisabled();
  });

  it('can still turn off while the local branch differs', () => {
    renderControl({ prMatch: 'differ', prAutoMerge: true });
    expect(toggle()).toBeEnabled();
  });

  it.each([
    [false, { on: true }],
    [true, { on: false }],
  ])('a click from %s posts %o', async (prAutoMerge, body) => {
    const server = renderControl({ prAutoMerge });
    await userEvent.click(toggle()!);
    await waitFor(() =>
      expect(server.requests).toContainEqual({
        method: 'POST',
        path: '/api/worktrees/northwind-alpha/auto-merge',
        body,
      }),
    );
  });

  it('stays busy until the switch reads the new state', async () => {
    const server = renderControl({ prAutoMerge: false });
    server.hold();
    await userEvent.click(toggle()!);
    // Let the post through, and hold the refetch it asks for.
    server.release();
    server.hold();
    await waitFor(() => expect(server.requests.map((r) => r.method)).toContain('POST'));
    expect(toggle()).toHaveAttribute('aria-busy', 'true');
    expect(toggle()).toHaveAttribute('aria-checked', 'false');
    server.release();
    await waitFor(() => expect(toggle()).not.toHaveAttribute('aria-busy'));
    expect(toggle()).toHaveAttribute('aria-checked', 'true');
  });

  it("shows Failed with GitHub's words when the server refuses", async () => {
    const server = renderControl({ prAutoMerge: false });
    server.refuse(/POST \/api\/worktrees\/[^/]+\/auto-merge$/, {
      status: 400,
      code: 'invalid',
      message: 'Auto merge is not allowed for this repository',
    });
    await userEvent.click(toggle()!);
    expect(await screen.findByText('Failed')).toBeInTheDocument();
    expect(screen.getByRole('switch')).toHaveAttribute(
      'title',
      'Auto merge is not allowed for this repository',
    );
  });
});
