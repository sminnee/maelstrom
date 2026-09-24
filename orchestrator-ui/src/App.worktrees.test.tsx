import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderApp } from './test/renderApp';

describe('the worktrees view', () => {
  const goToWorktrees = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(screen.getByRole('button', { name: 'Worktrees' }));
    return screen.getByTestId('worktree-table');
  };
  const row = (id: string) =>
    document.querySelector(
      `[data-testid="worktree-table"] [data-worktree-id="${id}"]`,
    ) as HTMLElement | null;
  const listedIds = () =>
    within(screen.getByTestId('worktree-table'))
      .getAllByRole('row')
      .map((r) => r.getAttribute('data-worktree-id'))
      .filter(Boolean)
      .sort();

  it('draws every open worktree, including ones with nothing on the desk', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToWorktrees(user);

    // Six open of the nine seeded; the three closed are held back.
    expect(listedIds()).toEqual([
      '_main',
      'maelstrom-alpha',
      'maelstrom-bravo',
      'northwind-alpha',
      'northwind-bravo',
      'northwind-delta',
    ]);
  });

  it('groups the rows under their project', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToWorktrees(user);

    const headings = within(screen.getByTestId('worktree-table'))
      .getAllByRole('heading')
      .map((h) => h.textContent);
    expect(headings).toEqual(['maelstrom', 'northwind']);
  });

  it('lists a closed worktree only when asked', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToWorktrees(user);
    expect(row('maelstrom-charlie')).toBeNull();

    await user.click(screen.getByRole('checkbox', { name: 'show closed' }));
    await waitFor(() => expect(row('maelstrom-charlie')).not.toBeNull());
    expect(row('maelstrom-charlie')).toHaveAttribute('data-closed', 'true');
  });

  it('reads the branch, the dirty count and the PR off the world', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToWorktrees(user);

    expect(row('maelstrom-alpha')).toHaveTextContent('feat/orchestrator-ui');
    expect(row('maelstrom-alpha')).toHaveTextContent('3');
    expect(row('northwind-delta')).toHaveTextContent('#118');
  });

  it('reads the remote column from the PR when there is one, and the push when there is not', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToWorktrees(user);

    // `cli.pr_display` reads the same two fields, so the table and the
    // terminal give one reading of one branch.
    const cell = (id: string) => within(row(id)!).getAllByRole('cell')[4]!;
    expect(cell('northwind-delta')).toHaveTextContent('4'); // PR open: prCommits
    expect(cell('maelstrom-bravo')).toHaveTextContent('2'); // no PR: pushedCommits
    expect(cell('northwind-alpha')).toHaveTextContent(''); // neither
  });

  it('offers no close or delete on _main, which holds the main checkout', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToWorktrees(user);

    const main = within(row('_main')!);
    expect(main.queryByRole('button', { name: 'Close' })).toBeNull();
    expect(main.queryByRole('button', { name: 'Delete' })).toBeNull();
    // It still syncs: _main is a checkout like any other.
    expect(main.getByRole('button', { name: 'Sync' })).toBeInTheDocument();
  });

  it('closes a worktree and the row goes with it', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToWorktrees(user);

    await user.click(within(row('northwind-alpha')!).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(row('northwind-alpha')).toBeNull());
  });

  it('says what the model said when a close is refused', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToWorktrees(user);

    // maelstrom-alpha has 3 dirty files, which the close refuses.
    await user.click(within(row('maelstrom-alpha')!).getByRole('button', { name: 'Close' }));
    await waitFor(() =>
      expect(within(row('maelstrom-alpha')!).getByTitle(/uncommitted changes/)).toBeInTheDocument(),
    );
    expect(row('maelstrom-alpha')).not.toBeNull();
  });

  it('asks before it force closes', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToWorktrees(user);

    await user.click(within(row('maelstrom-alpha')!).getByRole('button', { name: 'Force close' }));
    expect(screen.getByText(/Force close alpha\?/)).toBeInTheDocument();
    // Still there: asking is not doing.
    expect(row('maelstrom-alpha')).not.toBeNull();

    // Scoped to the question: the trigger carries the same word.
    const question = screen.getByRole('alertdialog');
    await user.click(within(question).getByRole('button', { name: 'Force close' }));
    await waitFor(() => expect(row('maelstrom-alpha')).toBeNull());
  });

  it('asks before it deletes, and the worktree then leaves the world', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToWorktrees(user);

    await user.click(within(row('northwind-bravo')!).getByRole('button', { name: 'Delete' }));
    // The question names the worktree and what survives it.
    expect(screen.getByText(/Delete bravo\?/)).toBeInTheDocument();
    expect(row('northwind-bravo')).not.toBeNull();

    await user.click(
      within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Delete it' }),
    );
    await waitFor(() => expect(row('northwind-bravo')).toBeNull());
  });

  it('starts a stopped environment, and the button becomes Stop', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToWorktrees(user);

    await user.click(within(row('northwind-alpha')!).getByRole('button', { name: 'Start env' }));
    await waitFor(() =>
      expect(
        within(row('northwind-alpha')!).getByRole('button', { name: 'Stop env' }),
      ).toBeInTheDocument(),
    );
  });

  it('offers Stop and Restart where an environment is running, and links the dev env', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToWorktrees(user);

    const delta = within(row('northwind-delta')!);
    expect(delta.getByRole('button', { name: 'Stop env' })).toBeInTheDocument();
    expect(delta.getByRole('button', { name: 'Restart' })).toBeInTheDocument();
    expect(delta.getByRole('link', { name: /Dev env/ })).toHaveAttribute(
      'href',
      'http://localhost:4210',
    );
  });

  it('syncs a worktree, asking for the mode that repairs a conflict', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    await goToWorktrees(user);

    await user.click(within(row('northwind-alpha')!).getByRole('button', { name: 'Sync' }));
    await waitFor(() =>
      expect(server.requests.some((r) => r.path === '/api/worktrees/northwind-alpha/sync')).toBe(
        true,
      ),
    );
    const sync = server.requests.find((r) => r.path === '/api/worktrees/northwind-alpha/sync')!;
    expect(sync.method).toBe('POST');
    expect(sync.body).toEqual({ mode: 'autorepair' });
  });

  it('refuses a sync on a closed worktree, as the server does', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToWorktrees(user);
    await user.click(screen.getByRole('checkbox', { name: 'show closed' }));
    await waitFor(() => expect(row('maelstrom-charlie')).not.toBeNull());

    // A closed worktree holds no branch, so it is offered no sync at all.
    expect(within(row('maelstrom-charlie')!).queryByRole('button', { name: 'Sync' })).toBeNull();
    // Deleting one is still the point of listing it.
    expect(
      within(row('maelstrom-charlie')!).getByRole('button', { name: 'Delete' }),
    ).toBeInTheDocument();
  });

  it('honours the project filter', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToWorktrees(user);

    await user.selectOptions(screen.getByRole('combobox', { name: 'Project' }), 'northwind');
    await waitFor(() => expect(row('maelstrom-alpha')).toBeNull());
    expect(row('northwind-alpha')).not.toBeNull();
  });

  it('offers no branch filter, whose options come from tasks alone', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToWorktrees(user);

    expect(screen.queryByRole('combobox', { name: 'Branch' })).toBeNull();
  });
});
