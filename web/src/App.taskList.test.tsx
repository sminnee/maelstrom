import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { chipCount } from './test/appHelpers';
import { renderApp } from './test/renderApp';
import { seedWorld } from './test/seedWorld';

describe('the task list', () => {
  const goToList = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(screen.getByRole('button', { name: 'Task list' }));
    return screen.getByTestId('task-list');
  };
  const listRow = (taskId: string) =>
    document.querySelector(`[data-testid="task-list"] [data-task-id="${taskId}"]`)!;
  const listedIds = () =>
    within(screen.getByTestId('task-list'))
      .getAllByRole('row')
      .map((r) => r.getAttribute('data-task-id'))
      .filter(Boolean)
      .sort();
  /** Tick every status back on, so finished tasks are listed too. */
  const showEveryStatus = async (user: ReturnType<typeof userEvent.setup>) => {
    for (const status of ['done', 'cancelled', 'template']) {
      await user.click(screen.getByRole('checkbox', { name: status }));
    }
  };

  it('opens on live work, and ticking the rest lists every task in the world', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToList(user);
    const tasks = Object.values(seedWorld().world.tasks);
    const live = tasks.filter((t) => ['todo', 'in-progress', 'blocked'].includes(t.status));
    expect(live.length).toBeLessThan(tasks.length);
    expect(listedIds()).toEqual(live.map((t) => t.id).sort());

    await showEveryStatus(user);
    expect(listedIds()).toEqual(tasks.map((t) => t.id).sort());
  });

  it('adds a task to the desk, and it is then drawn on the canvas', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToList(user);
    await showEveryStatus(user);
    expect(listRow('NORT-3')).toHaveAttribute('data-on-desk', 'false');

    await user.click(
      within(listRow('NORT-3') as HTMLElement).getByRole('button', { name: 'Add to desk' }),
    );
    await waitFor(() => expect(listRow('NORT-3')).toHaveAttribute('data-on-desk', 'true'));

    await user.click(screen.getByRole('button', { name: 'Canvas' }));
    expect(document.querySelector('[data-task-id="NORT-3"]')).toBeInTheDocument();
  });

  it('removes a task with no agent from the desk, and it leaves the canvas', async () => {
    const user = userEvent.setup();
    await renderApp();
    expect(document.querySelector('[data-task-id="NORT-9.1"]')).toBeInTheDocument();

    await goToList(user);
    await user.click(
      within(listRow('NORT-9.1') as HTMLElement).getByRole('button', { name: 'Remove from desk' }),
    );
    await waitFor(() => expect(listRow('NORT-9.1')).toHaveAttribute('data-on-desk', 'false'));

    await user.click(screen.getByRole('button', { name: 'Canvas' }));
    expect(document.querySelector('[data-task-id="NORT-9.1"]')).not.toBeInTheDocument();
  });

  it('a task removed while its agent runs stays on the canvas until it stops', async () => {
    const user = userEvent.setup();
    await renderApp();

    await goToList(user);
    await user.click(
      within(listRow('NORT-9') as HTMLElement).getByRole('button', { name: 'Remove from desk' }),
    );
    await waitFor(() => expect(listRow('NORT-9')).toHaveAttribute('data-on-desk', 'false'));

    await user.click(screen.getByRole('button', { name: 'Canvas' }));
    expect(document.querySelector('[data-task-id="NORT-9"]')).toBeInTheDocument();
  });

  it('shows a status as text until it is clicked, then moves the task', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToList(user);
    const row = () => listRow('NORT-9') as HTMLElement;
    expect(within(row()).queryByRole('combobox')).toBeNull();

    await user.click(within(row()).getByRole('button', { name: 'in-progress' }));
    await user.selectOptions(within(row()).getByRole('combobox'), 'blocked');

    expect(await within(row()).findByRole('button', { name: 'blocked' })).toBeInTheDocument();
    expect(within(row()).queryByRole('combobox')).toBeNull();
  });

  it('a task moved to done leaves the list, and the done filter brings it back', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToList(user);

    await user.click(
      within(listRow('NORT-9') as HTMLElement).getByRole('button', { name: 'in-progress' }),
    );
    await user.selectOptions(
      within(listRow('NORT-9') as HTMLElement).getByRole('combobox'),
      'done',
    );

    // The default filter hides done work, so the row goes. That is the filter
    // doing its job, not the move failing.
    await waitFor(() => expect(listRow('NORT-9')).toBeNull());
    await user.click(screen.getByRole('checkbox', { name: 'done' }));
    expect(listRow('NORT-9')).not.toBeNull();
    expect(
      within(listRow('NORT-9') as HTMLElement).getByRole('button', { name: 'done' }),
    ).toBeInTheDocument();
  });

  it('closes the status picker on Escape without moving the task', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToList(user);
    const row = () => listRow('NORT-9') as HTMLElement;

    await user.click(within(row()).getByRole('button', { name: 'in-progress' }));
    await user.keyboard('{Escape}');

    expect(within(row()).queryByRole('combobox')).toBeNull();
    expect(within(row()).getByRole('button', { name: 'in-progress' })).toBeInTheDocument();
  });

  it('opens the editor seeded from the task, and saving writes the new title', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToList(user);
    await user.click(
      within(listRow('NORT-9') as HTMLElement).getByRole('button', { name: 'Edit' }),
    );

    // The editor fetches the task's prose, so the form follows the click.
    const editor = await screen.findByRole('dialog', { name: 'Migrate to Postgres 16' });
    const title = within(editor).getByLabelText('Title');
    expect(title).toHaveValue('Migrate to Postgres 16');
    await user.clear(title);
    await user.type(title, 'Migrate to Postgres 17');
    await user.click(within(editor).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(listRow('NORT-9')).toHaveTextContent('Migrate to Postgres 17'));
  });

  it('leaves a task that names no model inheriting the default', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    await goToList(user);
    await user.click(
      within(listRow('NORT-9') as HTMLElement).getByRole('button', { name: 'Edit' }),
    );

    // `docs/guide/planning.md` asks for an unset model on execute drafts, so
    // opening one must not pin it. Saving an unrelated field sends no model.
    const editor = await screen.findByRole('dialog', { name: 'Migrate to Postgres 16' });
    await user.click(within(editor).getByText('Advanced'));
    expect(within(editor).getByLabelText('Model')).toHaveValue('');
    const title = within(editor).getByLabelText('Title');
    await user.clear(title);
    await user.type(title, 'Migrate to Postgres 17');
    await user.click(within(editor).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    const patch = server.requests.filter((r) => r.method === 'PATCH').at(-1);
    expect(patch!.body).not.toHaveProperty('model');
  });

  it('can put a task back on the inherited default', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    server.world.tasks['NORT-9']!.model = 'opus';
    await goToList(user);
    await user.click(
      within(listRow('NORT-9') as HTMLElement).getByRole('button', { name: 'Edit' }),
    );

    const editor = await screen.findByRole('dialog', { name: 'Migrate to Postgres 16' });
    await user.click(within(editor).getByText('Advanced'));
    await user.selectOptions(within(editor).getByLabelText('Model'), '');
    await user.click(within(editor).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    const patch = server.requests.filter((r) => r.method === 'PATCH').at(-1);
    expect(patch!.body).toMatchObject({ model: '' });
  });

  it('keeps a stored model the shortlist does not name', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    // The editor fetches the task itself, so seeding the stored model here
    // reaches it: this is a value written before the shortlist existed.
    server.world.tasks['NORT-9']!.model = 'claude-opus-4-1-20250805';
    await goToList(user);
    await user.click(
      within(listRow('NORT-9') as HTMLElement).getByRole('button', { name: 'Edit' }),
    );

    // The notebook's model field is free-form, so a value this build does not
    // list is offered rather than dropped — otherwise opening the task would
    // quietly rewrite it.
    const editor = await screen.findByRole('dialog', { name: 'Migrate to Postgres 16' });
    await user.click(within(editor).getByText('Advanced'));
    expect(within(editor).getByLabelText('Model')).toHaveValue('claude-opus-4-1-20250805');
  });

  it('keeps the advanced fields folded away until they are asked for', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToList(user);
    await user.click(
      within(listRow('NORT-9') as HTMLElement).getByRole('button', { name: 'Edit' }),
    );

    const editor = await screen.findByRole('dialog', { name: 'Migrate to Postgres 16' });
    expect(within(editor).queryByLabelText('Command')).not.toBeVisible();
    await user.click(within(editor).getByText('Advanced'));
    expect(within(editor).getByLabelText('Command')).toBeVisible();
  });

  it('sends the fields the user changed, not those the world changed under them', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    await goToList(user);
    await user.click(
      within(listRow('NORT-9') as HTMLElement).getByRole('button', { name: 'Edit' }),
    );

    const editor = await screen.findByRole('dialog', { name: 'Migrate to Postgres 16' });
    const title = within(editor).getByLabelText('Title');
    await user.clear(title);
    await user.type(title, 'Migrate to Postgres 17');
    // The world moves while the editor is open: the branch changes elsewhere.
    server.change({ kind: 'task', ids: ['NORT-9'] }, (w) => {
      w.tasks['NORT-9'] = { ...w.tasks['NORT-9']!, branch: 'feat/db-migrate-2' };
    });
    await waitFor(() => expect(listRow('NORT-9')).toHaveTextContent('feat/db-migrate-2'));
    await user.click(within(editor).getByRole('button', { name: 'Save' }));

    // The title the user typed lands; the branch they never touched is not
    // overwritten with the value the editor opened on.
    await waitFor(() => expect(listRow('NORT-9')).toHaveTextContent('Migrate to Postgres 17'));
    expect(listRow('NORT-9')).toHaveTextContent('feat/db-migrate-2');
    const patch = server.requests.find((r) => r.method === 'PATCH');
    expect(patch?.body).toEqual({ title: 'Migrate to Postgres 17' });
  });

  it('closes the editor on Escape when nothing was typed', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToList(user);
    await user.click(
      within(listRow('NORT-9') as HTMLElement).getByRole('button', { name: 'Edit' }),
    );
    await screen.findByRole('dialog', { name: 'Migrate to Postgres 16' });

    await user.keyboard('{Escape}');

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(listRow('NORT-9')).toHaveTextContent('Migrate to Postgres 16');
  });

  it('asks before it throws away typed edits, and keeps them if you say no', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToList(user);
    await user.click(
      within(listRow('NORT-9') as HTMLElement).getByRole('button', { name: 'Edit' }),
    );

    const title = within(
      await screen.findByRole('dialog', { name: 'Migrate to Postgres 16' }),
    ).getByLabelText('Title');
    await user.clear(title);
    await user.type(title, 'Never saved');
    await user.keyboard('{Escape}');

    // The editor stays, holding what was typed, until the discard is confirmed.
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(within(screen.getByRole('dialog')).getByLabelText('Title')).toHaveValue('Never saved');
    await user.click(screen.getByRole('button', { name: 'Keep editing' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('button', { name: 'Discard' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(listRow('NORT-9')).toHaveTextContent('Migrate to Postgres 16');
  });

  it('the attention chip still counts an agent blocked on an off-desk task', async () => {
    const user = userEvent.setup();
    await renderApp();
    const before = chipCount();
    expect(before).toBeGreaterThan(0);

    // Clear the desk, so the chip is counted against off-desk work.
    await goToList(user);
    for (const r of Array.from(
      document.querySelectorAll('[data-testid="task-list"] [data-on-desk="true"]'),
    )) {
      await user.click(within(r as HTMLElement).getByRole('button', { name: 'Remove from desk' }));
    }
    await waitFor(() => expect(document.querySelectorAll('[data-on-desk="true"]')).toHaveLength(0));
    expect(chipCount()).toBe(before);

    // Following the chip puts its task back on the desk so it has a node.
    await user.click(screen.getByTestId('attention-chip'));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('the attention chip returns to the canvas and expands the node', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToList(user);
    expect(screen.queryByTestId('task-node')).not.toBeInTheDocument();

    await user.click(screen.getByTestId('attention-chip'));
    expect(screen.getByRole('button', { name: 'Canvas' })).toHaveAttribute('aria-pressed', 'true');
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });
});
