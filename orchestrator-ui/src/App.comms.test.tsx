import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { paneItem } from './test/appHelpers';
import { renderApp } from './test/renderApp';

describe('the comms view', () => {
  const goToComms = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(paneItem('Comms'));
    return screen.getByTestId('comm-list');
  };
  const row = (id: string) =>
    document.querySelector(
      `[data-testid="comm-list"] [data-comm-id="${id}"]`,
    ) as HTMLElement | null;
  const listedIds = () =>
    [...document.querySelectorAll('[data-testid="comm-list"] [data-comm-id]')].map((r) =>
      r.getAttribute('data-comm-id'),
    );

  it('lists each open comm with its recipients, its task count and its highest landing', async () => {
    const user = userEvent.setup();
    await renderApp({ scenario: 'comms' });
    await goToComms(user);

    expect(listedIds()).toEqual(['c1', 'c2']);
    expect(row('c1')).toHaveTextContent('Order export is live');
    expect(row('c1')).toHaveTextContent('#sales');
    expect(row('c1')).toHaveTextContent('ops@northwind.test');
    expect(row('c1')).toHaveTextContent('2 tasks');
    // NORT-20 is live and NORT-21 has reached UAT: the comm reads its furthest task.
    expect(within(row('c1')!).getByTestId('comm-landing')).toHaveTextContent('live');
    // NORT-22 merged, NORT-23 done, NORT-12 not done.
    expect(row('c2')).toHaveTextContent('3 tasks');
    expect(within(row('c2')!).getByTestId('comm-landing')).toHaveTextContent('merged');
  });

  it('lists a closed comm only when asked', async () => {
    const user = userEvent.setup();
    await renderApp({ scenario: 'comms' });
    await goToComms(user);
    expect(row('c3')).toBeNull();

    await user.click(screen.getByRole('checkbox', { name: 'Show closed' }));
    expect(row('c3')).toHaveAttribute('data-closed', 'true');
  });

  it('says so when there is no comm', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToComms(user);
    await waitFor(() => expect(screen.getByTestId('comm-list')).toHaveTextContent('No comms yet.'));
  });

  it('writes a new comm, edits its recipients, links a task and closes it', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp({ scenario: 'comms' });
    await goToComms(user);
    const created = () => listedIds().filter((id) => id?.startsWith('new'));

    // Write it.
    await user.click(screen.getByRole('button', { name: 'New comm' }));
    let editor = within(screen.getByRole('dialog', { name: 'New comm' }));
    await user.type(editor.getByLabelText('Title'), 'Tell finance about refunds');
    await user.type(editor.getByLabelText('Recipients'), '#finance{Enter}');
    await user.click(editor.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(created()).toHaveLength(1));
    const id = created()[0]!;
    expect(server.world.comms[id]).toMatchObject({
      title: 'Tell finance about refunds',
      recipients: ['#finance'],
    });

    // The editor stays open on it, where the links are.
    editor = within(await screen.findByRole('dialog', { name: 'Tell finance about refunds' }));
    expect(editor.getByText('No task is linked yet.')).toBeInTheDocument();

    // Edit its recipients: drop one, add one.
    await user.click(editor.getByRole('button', { name: 'Remove #finance' }));
    await user.type(editor.getByLabelText('Recipients'), 'cfo@northwind.test{Enter}');
    await user.click(editor.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(row(id)).toHaveTextContent('cfo@northwind.test'));
    expect(row(id)).not.toHaveTextContent('#finance');

    // Link a task that has merged, and read its landing strip.
    await user.click(within(row(id)!).getByRole('button', { name: 'Tell finance about refunds' }));
    editor = within(await screen.findByRole('dialog', { name: 'Tell finance about refunds' }));
    await user.type(editor.getByLabelText('Link a task'), 'NORT-22');
    await user.click(editor.getByRole('button', { name: 'Link' }));
    await waitFor(() => expect(server.world.tasks['NORT-22']!.comms).toEqual(['c2', id]));
    const linked = await waitFor(() => {
      const item = screen.getByRole('dialog').querySelector('[data-task-id="NORT-22"]');
      expect(item).not.toBeNull();
      return item as HTMLElement;
    });
    expect(within(linked).getByTestId('landing-strip')).toHaveTextContent(
      'merged · UAT ○ · live ?',
    );
    await waitFor(() =>
      expect(within(row(id)!).getByTestId('comm-landing')).toHaveTextContent('merged'),
    );

    // Close comm would close the editor too, so it waits while an edit is unsaved.
    await user.type(editor.getByLabelText('Title'), '!');
    expect(editor.getByRole('button', { name: 'Close comm' })).toBeDisabled();
    await user.type(editor.getByLabelText('Title'), '{Backspace}');

    // Close it: it leaves the open list, and "Show closed" brings it back.
    await user.click(editor.getByRole('button', { name: 'Close comm' }));
    await waitFor(() => expect(row(id)).toBeNull());
    expect(server.world.comms[id]!.closedAt).not.toBe('');
    await user.click(screen.getByRole('checkbox', { name: 'Show closed' }));
    expect(row(id)).toHaveAttribute('data-closed', 'true');
  });

  it("fills a new comm's project from its category's default project", async () => {
    const user = userEvent.setup();
    const { server } = await renderApp({ scenario: 'comms', url: '/comms?project=maelstrom' });

    await user.click(await screen.findByRole('button', { name: 'New comm' }));
    const editor = within(screen.getByRole('dialog', { name: 'New comm' }));
    const project = editor.getByLabelText('Project') as HTMLSelectElement;
    await user.type(editor.getByLabelText('Title'), 'Tell support');
    // c1 and c2 are release comms for northwind; c3, closed, is support for riverbend.
    await user.type(editor.getByLabelText('Category'), 'support');
    expect(project.value).toBe('riverbend');
    await user.clear(editor.getByLabelText('Category'));
    await user.type(editor.getByLabelText('Category'), 'release');
    expect(project.value).toBe('northwind');
    // A category no comm uses has no default project, so the filter bar's project stands.
    await user.clear(editor.getByLabelText('Category'));
    await user.type(editor.getByLabelText('Category'), 'brand new');
    expect(project.value).toBe('maelstrom');

    // A project the user picked stays, whatever the category becomes.
    await user.selectOptions(project, 'riverbend');
    await user.selectOptions(project, 'maelstrom');
    await user.clear(editor.getByLabelText('Category'));
    await user.type(editor.getByLabelText('Category'), 'support');
    expect(project.value).toBe('maelstrom');

    await user.click(editor.getByRole('button', { name: 'Create' }));
    await waitFor(() =>
      expect(Object.values(server.world.comms).find((c) => c.id.startsWith('new'))).toMatchObject({
        category: 'support',
        project: 'maelstrom',
      }),
    );
  });

  it('makes a task from a comm, linked to it, through New work', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp({ scenario: 'comms' });
    await goToComms(user);

    await user.click(within(row('c1')!).getByRole('button', { name: 'Order export is live' }));
    const editor = within(await screen.findByRole('dialog', { name: 'Order export is live' }));
    await user.click(editor.getByRole('button', { name: 'New task' }));

    const form = within(await screen.findByRole('dialog', { name: 'New work' }));
    expect(form.getByLabelText('Title')).toHaveValue('Order export is live');
    expect(form.getByLabelText('What needs doing?')).toHaveValue(
      'Sales asked to hear when customers can download their orders.',
    );
    expect(form.getByRole('radio', { name: 'northwind' })).toBeChecked();
    expect(form.getByTestId('new-work-comms')).toHaveTextContent(
      'Links to c1 · Order export is live',
    );
    await user.click(form.getByRole('button', { name: 'Save' }));

    const made = await waitFor(() => {
      const task = Object.values(server.world.tasks).find((t) => t.id.includes('/NEW-'));
      expect(task).toBeDefined();
      return task!;
    });
    expect(made.comms).toEqual(['c1']);
    await waitFor(() =>
      expect(
        screen
          .getByRole('dialog', { name: 'Order export is live' })
          .querySelector(`[data-task-id="${made.id}"]`),
      ).not.toBeNull(),
    );
  });

  it("keeps the prose New work already holds over the comm's content", async () => {
    const user = userEvent.setup();
    await renderApp({ scenario: 'comms' });
    await user.click(screen.getByRole('button', { name: 'New' }));
    let form = within(await screen.findByRole('dialog', { name: 'New work' }));
    await user.type(form.getByLabelText('What needs doing?'), 'My own words');
    await user.click(form.getByRole('button', { name: 'Cancel' }));

    await goToComms(user);
    await user.click(within(row('c1')!).getByRole('button', { name: 'Order export is live' }));
    const editor = within(await screen.findByRole('dialog', { name: 'Order export is live' }));
    await user.click(editor.getByRole('button', { name: 'New task' }));
    form = within(await screen.findByRole('dialog', { name: 'New work' }));
    expect(form.getByLabelText('What needs doing?')).toHaveValue('My own words');
    expect(form.getByLabelText('Title')).toHaveValue('Order export is live');
  });

  it("replaces one comm's seeded prose with the next comm's", async () => {
    const user = userEvent.setup();
    await renderApp({ scenario: 'comms' });
    await goToComms(user);
    const newTaskFrom = async (title: string) => {
      await user.click(within(document.body).getByRole('button', { name: title }));
      const editor = within(await screen.findByRole('dialog', { name: title }));
      await user.click(editor.getByRole('button', { name: 'New task' }));
      return within(await screen.findByRole('dialog', { name: 'New work' }));
    };

    let form = await newTaskFrom('Order export is live');
    await user.click(form.getByRole('button', { name: 'Cancel' }));
    await user.keyboard('{Escape}');
    form = await newTaskFrom('Refunds retry on their own');
    expect(form.getByLabelText('What needs doing?')).toHaveValue(
      'Support wants to stop retrying failed refunds by hand.',
    );
    expect(form.getByTestId('new-work-comms')).toHaveTextContent('Links to c2');
    expect(form.getByTestId('new-work-comms')).not.toHaveTextContent('c1');
  });

  it("offers the tasks viewed most recently first in a comm's task picker", async () => {
    const user = userEvent.setup();
    const { router } = await renderApp({ scenario: 'comms', url: '/tasks?edit=NORT-23' });
    await screen.findByRole('dialog', { name: 'Reword the refund email' });
    await router.navigate('/tasks?edit=NORT-22');
    await screen.findByRole('dialog', { name: 'Retry a failed refund webhook' });
    // NORT-20 is viewed last, but c1 already links it, so it is not offered.
    await router.navigate('/tasks?edit=NORT-20');
    await screen.findByRole('dialog', { name: 'Export orders as CSV' });
    await router.navigate('/tasks');
    await goToComms(user);

    await user.click(within(row('c1')!).getByRole('button', { name: 'Order export is live' }));
    const editor = within(await screen.findByRole('dialog', { name: 'Order export is live' }));
    await user.click(editor.getByLabelText('Link a task'));
    const offer = screen.getByRole('listbox');
    expect(offer).toHaveTextContent('Recent');
    expect(
      within(offer)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['NORT-22Retry a failed refund webhook', 'NORT-23Reword the refund email']);

    // Typing searches every task, not only the recent ones.
    await user.type(editor.getByLabelText('Link a task'), 'NORT-2');
    expect(screen.getByRole('listbox')).not.toHaveTextContent('Recent');
    expect(within(screen.getByRole('listbox')).getAllByRole('option').length).toBeGreaterThan(1);
  });

  it("links and unlinks comms from a task's editor, recent comms first", async () => {
    const user = userEvent.setup();
    const { server, router } = await renderApp({ scenario: 'comms' });
    await goToComms(user);
    // Viewing c1 is what puts it first in the task's picker.
    await user.click(within(row('c1')!).getByRole('button', { name: 'Order export is live' }));
    await screen.findByRole('dialog', { name: 'Order export is live' });
    await user.keyboard('{Escape}');

    await router.navigate('/tasks?edit=NORT-12');
    const dialog = await screen.findByRole('dialog', {
      name: server.world.tasks['NORT-12']!.title,
    });
    const comms = within(within(dialog).getByRole('region', { name: 'Comms' }));
    expect(comms.getByText('Refunds retry on their own')).toBeInTheDocument();

    await user.click(comms.getByLabelText('Link a comm'));
    const offer = screen.getByRole('listbox');
    expect(offer).toHaveTextContent('Recent');
    expect(
      within(offer)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['c1Order export is live']);
    await user.click(within(offer).getByRole('option'));
    await user.click(comms.getByRole('button', { name: 'Link' }));
    await waitFor(() => expect(server.world.tasks['NORT-12']!.comms).toEqual(['c2', 'c1']));
    await waitFor(() => expect(comms.getByText('Order export is live')).toBeInTheDocument());

    await user.click(comms.getByRole('button', { name: 'Unlink c2' }));
    await waitFor(() => expect(server.world.tasks['NORT-12']!.comms).toEqual(['c1']));
  });

  it('unlinks a task from a comm', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp({ scenario: 'comms' });
    await goToComms(user);

    await user.click(within(row('c1')!).getByRole('button', { name: 'Order export is live' }));
    const editor = within(await screen.findByRole('dialog', { name: 'Order export is live' }));
    await user.click(editor.getByRole('button', { name: 'Unlink NORT-20' }));

    await waitFor(() => expect(server.world.tasks['NORT-20']!.comms).toEqual([]));
    await waitFor(() => expect(row('c1')).toHaveTextContent('1 task'));
    // NORT-21 is at UAT, so that is now the furthest the comm has landed.
    expect(within(row('c1')!).getByTestId('comm-landing')).toHaveTextContent('uat');
  });

  it('is a view on the narrow layout too', async () => {
    const user = userEvent.setup();
    await renderApp({ scenario: 'comms', viewport: 'narrow' });
    await goToComms(user);
    expect(listedIds()).toEqual(['c1', 'c2']);
  });
});
