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
