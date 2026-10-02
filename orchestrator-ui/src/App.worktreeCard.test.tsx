import { describe, expect, it } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { FakeServer } from './test/fakeServer';
import { commandsSince, expanded, worktreeControls } from './test/appHelpers';
import { useAppStore } from './store/store';
import { clickNode, pressKey, renderApp } from './test/renderApp';

/** Open `northwind-charlie` on a branch. It holds no node, so its box is an empty one. */
function openCharlie(server: FakeServer, branch = 'feat/idle') {
  server.change({ kind: 'worktree', ids: ['northwind-charlie'] }, (w) => {
    w.worktrees['northwind-charlie'] = {
      ...w.worktrees['northwind-charlie']!,
      isClosed: false,
      branch,
    };
  });
}

const box = (id: string) =>
  document.querySelector(`[data-testid="worktree-box"][data-worktree-id="${id}"]`) as HTMLElement;

/**
 * Click a box's label. fireEvent, not user-event: a canvas mousedown reaches
 * React Flow's d3-zoom, which jsdom cannot run — see `clickNode`.
 */
function clickLabel(id: string) {
  fireEvent.click(within(box(id)).getByRole('button'));
}

const card = () => screen.getByRole('dialog', { name: 'Worktree northwind charlie' });

/** The app with charlie open and empty, and its Worktree card open. */
async function renderWithCard(branch?: string) {
  const rendered = await renderApp();
  openCharlie(rendered.server, branch);
  await waitFor(() => expect(box('northwind-charlie')).toBeInTheDocument());
  clickLabel('northwind-charlie');
  return rendered;
}

describe('the worktree box label', () => {
  it('names the worktree and its branch', async () => {
    const { server } = await renderApp();
    expect(within(box('northwind-alpha')).getByRole('button')).toHaveTextContent(
      /^alpha\s*feat\/orders$/,
    );
    // A long branch is cut short by CSS, so the label carries it whole too.
    expect(within(box('northwind-delta')).getByRole('button')).toHaveAttribute(
      'title',
      'feat/rotate-auth-tokens-for-every-service',
    );
    openCharlie(server, '');
    await waitFor(() =>
      expect(within(box('northwind-charlie')).getByRole('button')).toHaveTextContent(
        /^charlie\s*\(detached\)$/,
      ),
    );
  });

  it('opens the Worktree card, and a second click or Esc collapses it', async () => {
    await renderWithCard();
    expect(card()).toHaveTextContent('charlie');
    expect(card()).toHaveTextContent('northwind');
    expect(within(card()).getByTestId('worktree-name')).toHaveTextContent('charlie · feat/idle');

    clickLabel('northwind-charlie');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    clickLabel('northwind-charlie');
    expect(card()).toBeInTheDocument();
    pressKey('Escape');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    clickLabel('northwind-charlie');
    fireEvent.click(within(card()).getByRole('button', { name: 'Collapse' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('shows one card at a time: a node card closes the Worktree card, and the reverse', async () => {
    await renderWithCard();
    clickNode('NORT-7');
    await waitFor(() =>
      expect(screen.getByRole('dialog', { name: 'Plan the order export' })).toBeInTheDocument(),
    );
    expect(screen.getAllByRole('dialog')).toHaveLength(1);

    clickLabel('northwind-charlie');
    await waitFor(() => expect(card()).toBeInTheDocument());
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
    expect(document.querySelector('[data-task-id="NORT-7"]')).not.toHaveAttribute('data-expanded');
  });

  it('opens on a box that holds nodes as it does on an empty one', async () => {
    await renderApp();
    clickLabel('northwind-alpha');
    const alpha = screen.getByRole('dialog', { name: 'Worktree northwind alpha' });
    expect(within(alpha).getByTestId('worktree-name')).toHaveTextContent('alpha · feat/orders');
  });

  // Nothing draws while the world loads, so "its box is gone" means nothing yet.
  it('keeps a card that was opened before the world loaded', async () => {
    const { server } = await renderApp({ ready: false });
    act(() => useAppStore.getState().expandWorktree('northwind-alpha'));
    server.release();
    expect(
      await screen.findByRole('dialog', { name: 'Worktree northwind alpha' }),
    ).toBeInTheDocument();
  });

  it('collapses when its worktree closes', async () => {
    const user = userEvent.setup();
    await renderWithCard();
    await user.click(within(card()).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(box('northwind-charlie')).toBeNull();
  });
});

describe('the Worktree card of a worktree with no node', () => {
  it('syncs, starts the environment and makes the terminal', async () => {
    const user = userEvent.setup();
    const { server } = await renderWithCard();
    const before = server.requests.length;

    await user.click(within(card()).getByRole('button', { name: 'Sync' }));
    await waitFor(() =>
      expect(commandsSince(server, before)).toEqual(['POST /api/worktrees/northwind-charlie/sync']),
    );
    await user.click(within(card()).getByRole('button', { name: 'Start env' }));
    await waitFor(() =>
      expect(within(card()).getByRole('button', { name: 'Stop env' })).toBeInTheDocument(),
    );
    expect(commandsSince(server, before).at(-1)).toBe('POST /api/worktrees/northwind-charlie/env');
    expect(within(card()).getByRole('link', { name: 'Changes' })).toBeInTheDocument();
    expect(within(card()).getByRole('button', { name: 'cmux' })).toBeInTheDocument();
  });

  it('asks before it shelves, and before it trashes', async () => {
    const user = userEvent.setup();
    const { server } = await renderWithCard();
    const before = server.requests.length;

    await user.click(within(card()).getByRole('button', { name: 'More close actions' }));
    await user.click(within(card()).getByRole('menuitem', { name: 'Shelve' }));
    const shelve = within(card()).getByRole('alertdialog', {
      name: 'Shelve charlie? Work in progress is committed. Unmerged work gets a task to reopen it.',
    });
    await user.click(within(shelve).getByRole('button', { name: 'Keep it' }));

    await user.click(within(card()).getByRole('button', { name: 'More close actions' }));
    await user.click(within(card()).getByRole('menuitem', { name: 'Trash' }));
    const trash = within(card()).getByRole('alertdialog', {
      name: 'Trash feat/idle? Its PR closes and the branch moves to trash/.',
    });
    await user.click(within(trash).getByRole('button', { name: 'Keep it' }));
    expect(commandsSince(server, before)).toEqual([]);

    await user.click(within(card()).getByRole('button', { name: 'More close actions' }));
    await user.click(within(card()).getByRole('menuitem', { name: 'Shelve' }));
    await user.click(within(card()).getByRole('button', { name: 'Shelve it' }));
    await waitFor(() =>
      expect(commandsSince(server, before)).toEqual([
        'POST /api/worktrees/northwind-charlie/force-close',
      ]),
    );
  });

  it('opens the New form on a free agent in this worktree, and keeps a held draft', async () => {
    const user = userEvent.setup();
    await renderApp();
    // A draft typed earlier, on another kind and another project.
    await user.click(screen.getByRole('button', { name: 'New' }));
    const first = screen.getByRole('dialog', { name: 'New work' });
    await user.click(within(first).getByRole('radio', { name: 'maelstrom' }));
    await user.type(within(first).getByLabelText('What needs doing?'), 'Tidy the index');
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());

    clickLabel('northwind-alpha');
    const alpha = screen.getByRole('dialog', { name: 'Worktree northwind alpha' });
    await user.click(within(alpha).getByRole('button', { name: 'Start free agent' }));

    const form = screen.getByRole('dialog', { name: 'New work' });
    expect(within(form).getByRole('radio', { name: 'Free agent' })).toBeChecked();
    expect(within(form).getByRole('radio', { name: 'northwind' })).toBeChecked();
    expect(within(form).getByLabelText('Branch')).toHaveValue('feat/orders');
    expect(within(form).getByLabelText('What needs doing?')).toHaveValue('Tidy the index');
    // The form is the one thing open: the card it came from has collapsed.
    expect(screen.queryByRole('dialog', { name: 'Worktree northwind alpha' })).toBeNull();
  });

  it('offers no free agent on a detached worktree, and says why', async () => {
    const user = userEvent.setup();
    await renderWithCard('');
    const start = within(card()).getByRole('button', { name: 'Start free agent' });
    expect(start).toBeDisabled();
    expect(start).toHaveAccessibleDescription('A detached worktree has no branch to start on');

    // With no branch to name, a trash names the worktree.
    await user.click(within(card()).getByRole('button', { name: 'More close actions' }));
    await user.click(within(card()).getByRole('menuitem', { name: 'Trash' }));
    expect(within(card()).getByRole('alertdialog')).toHaveAccessibleName(
      'Trash charlie? Its PR closes and the branch moves to trash/.',
    );
  });

  // A resize across the narrow breakpoint mounts the form again. The seed is
  // spent by then, so it does not undo what the user changed.
  it('lays the seed once: a later mount keeps what the user chose', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickLabel('northwind-alpha');
    await user.click(screen.getByRole('button', { name: 'Start free agent' }));
    const form = screen.getByRole('dialog', { name: 'New work' });
    await user.click(within(form).getByRole('radio', { name: 'Task' }));
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());

    await user.click(screen.getByRole('button', { name: 'New' }));
    expect(
      within(screen.getByRole('dialog', { name: 'New work' })).getByRole('radio', { name: 'Task' }),
    ).toBeChecked();
    expect(useAppStore.getState().ui.newWorkSeed).toBeNull();
  });
});

describe('a node card and a Worktree card of one worktree', () => {
  it('hold the same worktree controls', async () => {
    await renderApp();
    clickNode('NORT-12');
    const onNode = worktreeControls(expanded());
    clickLabel('northwind-delta');
    const onWorktree = worktreeControls(
      screen.getByRole('dialog', { name: 'Worktree northwind delta' }),
    );
    expect(onWorktree).toEqual(onNode);
  });
});
