import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { commandsSince, paneItem } from './test/appHelpers';
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

describe('the card of a node under an operation', () => {
  it('disables its commands while the operation runs', async () => {
    await renderApp({ scenario: 'operations-board' });
    await waitFor(() => expect(node('MAEL-52')).toHaveAttribute('data-operation', 'running'));
    clickNode('MAEL-52');
    const card = screen.getByRole('dialog');
    expect(card).toHaveTextContent('Closing alpha · 3/6');
    const commands = within(within(card).getByTestId('node-commands'));
    expect(commands.getByRole('button', { name: 'Edit task' })).toBeDisabled();
    expect(commands.getByRole('button', { name: 'Dismiss' })).toBeDisabled();
  });

  it('disables its worktree commands while a sync runs, though the node is not marked', async () => {
    await renderApp({ scenario: 'operations-board' });
    await waitFor(() => expect(box('northwind-bravo')).toHaveAttribute('data-operation'));
    clickNode('NORT-9');
    const card = screen.getByRole('dialog');
    expect(within(card).getByRole('button', { name: 'Sync' })).toBeDisabled();
    const commands = within(within(card).getByTestId('node-commands'));
    expect(commands.getByRole('button', { name: 'Edit task' })).toBeEnabled();
  });

  it('marks a refused close seen as it opens, and still offers the closes that do not refuse', async () => {
    const { server } = await renderApp({ scenario: 'operations-board' });
    await waitFor(() => expect(node('MAEL-40.1')).toHaveAttribute('data-operation', 'refused'));
    const before = server.requests.length;
    clickNode('MAEL-40.1');
    await waitFor(() =>
      expect(commandsSince(server, before)).toEqual(['POST /api/operations/op2/seen']),
    );
    await waitFor(() => expect(node('MAEL-40.1')).not.toHaveAttribute('data-operation'));

    // The card holds what it opened on: the reader is still reading it.
    const card = screen.getByRole('dialog');
    expect(card).toHaveTextContent('Close refused · Worktree has 2 commit(s) not merged');
    const next = within(within(card).getByRole('group', { name: 'Next' }));
    expect(next.getAllByRole('button').map((b) => b.textContent)).toEqual([
      'Shelve',
      'Trash',
      'Delete',
    ]);
    expect(
      within(within(card).getByTestId('node-commands')).getByRole('button', { name: 'Dismiss' }),
    ).toBeEnabled();
  });

  it('ends a next chain off the desk, as the Dismiss it stands in for', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp({ scenario: 'operations-board' });
    await waitFor(() => expect(node('MAEL-40.1')).toHaveAttribute('data-operation', 'refused'));
    clickNode('MAEL-40.1');
    const card = screen.getByRole('dialog');
    await waitFor(() => expect(node('MAEL-40.1')).not.toHaveAttribute('data-operation'));
    const before = server.requests.length;
    await user.click(within(card).getByRole('button', { name: 'Shelve' }));
    await user.click(within(card).getByRole('button', { name: 'Shelve it' }));
    await waitFor(() => expect(node('MAEL-40.1')).toBeNull());
    expect(commandsSince(server, before)).toEqual(['POST /api/desk/task:MAEL-40.1/dismiss']);
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

  it('says how an operation ended badly, until its card shows it', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp({ scenario: 'operations-board' });
    await waitFor(() =>
      expect(box('maelstrom-bravo')).toHaveAttribute('data-operation', 'refused'),
    );
    expect(within(box('maelstrom-bravo')!).getByRole('button')).toHaveTextContent(
      '· close refused',
    );
    expect(box('northwind-delta')).toHaveAttribute('data-operation', 'failed');

    const before = server.requests.length;
    await user.click(within(box('northwind-delta')!).getByRole('button'));
    await waitFor(() =>
      expect(commandsSince(server, before)).toEqual(['POST /api/operations/op3/seen']),
    );
    await waitFor(() => expect(box('northwind-delta')).not.toHaveAttribute('data-operation'));
    // The card holds what it marked seen, so the operator reads what failed.
    const card = screen.getByRole('dialog', { name: 'Worktree northwind delta' });
    expect(card).toHaveTextContent('Shelve failed · git push exited 1');
  });

  it('disables the worktree card commands while an operation runs', async () => {
    const user = userEvent.setup();
    await renderApp({ scenario: 'operations-board' });
    await waitFor(() => expect(box('northwind-bravo')).toHaveAttribute('data-operation'));
    await user.click(within(box('northwind-bravo')!).getByRole('button'));
    const card = screen.getByRole('dialog', { name: 'Worktree northwind bravo' });
    expect(card).toHaveTextContent('Syncing bravo · 2/3');
    expect(within(card).getByRole('button', { name: 'Start free agent' })).toBeDisabled();
    expect(within(card).getByRole('button', { name: 'Sync' })).toBeDisabled();
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
    expect(within(row('maelstrom-alpha')!).getByRole('button', { name: 'Sync' })).toBeDisabled();
    expect(within(row('northwind-alpha')!).getByRole('button', { name: 'Sync' })).toBeEnabled();
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

  it('takes the swipe away while the operation runs', async () => {
    const { server } = await renderApp({ scenario: 'operations-board', viewport: 'narrow' });
    // Stopped, the row would swipe to Dismiss, but its worktree is closing.
    server.change({ kind: 'agent', ids: ['b7d2e4a0'] }, (w) => {
      w.agents['b7d2e4a0'] = {
        ...w.agents['b7d2e4a0']!,
        state: 'exited',
        exitCode: 0,
        pendingRequestIds: [],
      };
    });
    // Its question went with it.
    const asks = Object.values(server.world.attention).filter((a) => a.agentId === 'b7d2e4a0');
    server.change({ kind: 'attention', ids: asks.map((a) => a.id) }, (w) => {
      for (const a of asks) delete w.attention[a.id];
    });
    await waitFor(() => expect(row('MAEL-52')).toHaveAttribute('data-state', 'stopped'));
    expect(row('MAEL-52')).toHaveAttribute('data-operation', 'running');
    expect(row('MAEL-52')).not.toHaveAttribute('data-swipe');

    // Once the close ends the swipe comes back: the operation alone held it.
    server.change({ kind: 'operation', ids: ['op1'] }, (w) => {
      w.operations['op1'] = { ...w.operations['op1']!, state: 'refused', seen: true };
    });
    await waitFor(() => expect(row('MAEL-52')).toHaveAttribute('data-swipe', 'dismiss'));
  });
});

describe('a Dismiss that runs a close', () => {
  it('hatches the node while the close runs, over a reload, then takes it off the desk', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const { server, reload } = await renderApp({ scenario: 'operations-board' });
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
    // The close's six steps, then off the desk: one operation.
    expect(node('NORT-7')).toHaveTextContent('Dismissing Plan the order export · 1/7');

    // The server runs the chain, so a reload keeps the hatch.
    await reload();
    await waitFor(() => expect(node('NORT-7')).toHaveAttribute('data-operation', 'running'));
    for (let step = 0; step < 7; step++) {
      act(() => {
        vi.advanceTimersByTime(60_000);
      });
    }
    await waitFor(() => expect(node('NORT-7')).toBeNull());
  });
});

describe('a launch that fails', () => {
  it('leaves the node where it was, marked failed, with its task back to todo', async () => {
    const user = userEvent.setup();
    await renderApp({ scenario: 'launch-fails' });
    clickNode('NORT-7.1');
    const card = screen.getByRole('dialog');
    await user.click(within(card).getByRole('button', { name: 'Launch' }));

    // The node is marked failed until the open card shows the failure, which
    // the card then holds once it marks it seen.
    await waitFor(() => expect(node('NORT-7.1')).toHaveAttribute('data-operation', 'failed'));
    await waitFor(() =>
      expect(card).toHaveTextContent('Launch failed · .env is not writable: Permission denied'),
    );
    // Rolled back to todo, so it is ready to launch again where it was.
    await waitFor(() => expect(node('NORT-7.1')).toHaveAttribute('data-state', 'ready'));
  });
});
