import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { paneItem } from './test/appHelpers';
import { clickNode, renderApp } from './test/renderApp';

/**
 * The `operations-board` scenario: a close runs on maelstrom-alpha (MAEL-52), a
 * close is refused on maelstrom-bravo (MAEL-40.1), a shelve failed on
 * northwind-delta (NORT-12), and a sync runs on northwind-bravo (NORT-9).
 */
const box = (id: string) =>
  document.querySelector<HTMLElement>(`[data-testid="worktree-box"][data-worktree-id="${id}"]`);
const node = (id: string) => document.querySelector<HTMLElement>(`[data-task-id="${id}"]`);
/** The state of each segment of a step meter, in order. */
const segments = (meter: HTMLElement) =>
  Array.from(meter.children).map((s) => s.getAttribute('data-state'));

afterEach(() => {
  vi.useRealTimers();
});

describe('a task node under an operation', () => {
  it('hatches while a close runs, with its words and the step it is on', async () => {
    await renderApp({ scenario: 'operations-board' });
    await waitFor(() => expect(node('MAEL-52')).toHaveAttribute('data-operation', 'running'));
    const running = node('MAEL-52')!;
    expect(running).toHaveTextContent('Closing alpha · 3/6');
    const meter = within(running).getByRole('img', { name: '2 of 6 steps done' });
    expect(segments(meter)).toEqual(['done', 'done', 'running', 'pending', 'pending', 'pending']);
  });

  it('reads Close refused and why once the close is refused', async () => {
    await renderApp({ scenario: 'operations-board' });
    await waitFor(() => expect(node('MAEL-40.1')).toHaveAttribute('data-operation', 'refused'));
    expect(node('MAEL-40.1')).toHaveTextContent(
      'Close refused · Worktree has 2 commit(s) not merged to origin/main',
    );
    expect(node('NORT-12')).toHaveAttribute('data-operation', 'failed');
  });

  it('stays unmarked while a sync runs on its worktree', async () => {
    await renderApp({ scenario: 'operations-board' });
    await waitFor(() => expect(node('MAEL-52')).toHaveAttribute('data-operation'));
    expect(node('NORT-9')).not.toHaveAttribute('data-operation');
    expect(node('NORT-9')).toHaveAttribute('data-state', 'working');
    expect(within(node('NORT-9')!).queryByRole('img', { name: /steps done/ })).toBeNull();
  });
});

describe('a worktree box under an operation', () => {
  it('says what runs on it in its label, for any kind', async () => {
    await renderApp({ scenario: 'operations-board' });
    await waitFor(() =>
      expect(box('maelstrom-alpha')).toHaveAttribute('data-operation', 'running'),
    );
    expect(within(box('maelstrom-alpha')!).getByRole('button')).toHaveTextContent(
      /^alpha .* · closing 3\/6$/,
    );
    // A sync marks the box, though it leaves the node in it unmarked.
    expect(box('northwind-bravo')).toHaveAttribute('data-operation', 'running');
    expect(within(box('northwind-bravo')!).getByRole('button')).toHaveTextContent('· syncing 2/3');
  });

  it('says how an operation ended badly', async () => {
    await renderApp({ scenario: 'operations-board' });
    await waitFor(() =>
      expect(box('maelstrom-bravo')).toHaveAttribute('data-operation', 'refused'),
    );
    expect(within(box('maelstrom-bravo')!).getByRole('button')).toHaveTextContent(
      '· close refused',
    );
    expect(box('northwind-delta')).toHaveAttribute('data-operation', 'failed');
  });
});

describe('a worktree table row under an operation', () => {
  const row = (id: string) =>
    document.querySelector<HTMLTableRowElement>(
      `[data-testid="worktree-table"] tr[data-worktree-id="${id}"]`,
    );

  it('says what runs on the worktree, with a step meter, and how it ended badly', async () => {
    const user = userEvent.setup();
    await renderApp({ scenario: 'operations-board' });
    await user.click(paneItem('Worktrees'));
    await waitFor(() =>
      expect(row('maelstrom-alpha')).toHaveAttribute('data-operation', 'running'),
    );
    const running = within(row('maelstrom-alpha')!.cells[0]!);
    expect(running.getByText('Closing alpha · 3/6')).toBeInTheDocument();
    expect(running.getByRole('img', { name: '2 of 6 steps done' })).toBeInTheDocument();
    expect(row('northwind-bravo')).toHaveTextContent('Syncing bravo · 2/3');
    expect(row('maelstrom-bravo')).toHaveAttribute('data-operation', 'refused');
    expect(row('maelstrom-bravo')!.cells[0]).toHaveTextContent(
      'Close refused · Worktree has 2 commit(s) not merged to origin/main',
    );
    expect(row('northwind-alpha')).not.toHaveAttribute('data-operation');
  });
});

describe('a deck row under an operation', () => {
  const row = (id: string) =>
    document.querySelector<HTMLElement>(`[data-testid="deck-row"][data-task-id="${id}"]`);

  it('hatches and speaks for the operation, as the node does', async () => {
    await renderApp({ scenario: 'operations-board', viewport: 'narrow' });
    await waitFor(() => expect(row('MAEL-52')).toHaveAttribute('data-operation', 'running'));
    expect(row('MAEL-52')).toHaveTextContent('Closing alpha · 3/6');
    expect(row('MAEL-40.1')).toHaveAttribute('data-operation', 'refused');
    expect(row('MAEL-40.1')).toHaveTextContent('Close refused · Worktree has 2 commit(s)');
    expect(row('NORT-9')).not.toHaveAttribute('data-operation');
  });
});

describe('a Dismiss that runs a close', () => {
  it('hatches the node while the close runs, then takes it off the desk', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const { server } = await renderApp({ scenario: 'operations-board' });
    // Each step waits on the fake clock, so the close holds its first step.
    server.stepMs = 60_000;
    server.change({ kind: 'agent', ids: ['a1f3c9e2'] }, (w) => {
      w.agents['a1f3c9e2'] = {
        ...w.agents['a1f3c9e2']!,
        state: 'exited',
        exitCode: 0,
        pendingRequestIds: [],
      };
    });
    clickNode('NORT-7');
    const card = screen.getByRole('dialog');
    const commands = within(within(card).getByTestId('node-commands'));
    await waitFor(() => expect(commands.getByRole('button', { name: 'Dismiss' })).toBeEnabled());
    await user.click(commands.getByRole('button', { name: 'Dismiss' }));

    await waitFor(() => expect(node('NORT-7')).toHaveAttribute('data-operation', 'running'));
    expect(node('NORT-7')).toHaveTextContent('Closing alpha · 1/6');
    for (let step = 0; step < 6; step++) {
      act(() => {
        vi.advanceTimersByTime(60_000);
      });
    }
    await waitFor(() => expect(node('NORT-7')).toBeNull());
  });
});
