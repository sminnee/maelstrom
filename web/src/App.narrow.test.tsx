import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { nodeState } from './test/appHelpers';
import { renderApp } from './test/renderApp';

describe('the narrow layout', () => {
  /** The deck list's rows, in the order they are drawn. */
  const deckRows = () =>
    screen.queryAllByTestId('deck-row').map((r) => r.getAttribute('data-task-id'));
  const zoneTab = (name: RegExp) => screen.getByRole('tab', { name });

  it('draws the deck list in place of the canvas, and no panel', async () => {
    await renderApp({ viewport: 'narrow' });
    expect(screen.getByTestId('deck-list')).toBeInTheDocument();
    expect(screen.queryByTestId('canvas')).not.toBeInTheDocument();
    expect(screen.queryByTestId('panel')).not.toBeInTheDocument();
  });

  it('reads the PR number on a deck row, as the canvas node does', async () => {
    await renderApp({ viewport: 'narrow' });
    const row = screen.getByTestId('deck-list').querySelector('[data-task-id="NORT-12"]');
    expect(row).toHaveTextContent('#118');
    // The chip carries its state in words here too, but not as a link: the
    // whole row is a button, and an anchor may not nest inside one.
    const chip = within(row as HTMLElement).getByLabelText('PR #118, CI running');
    expect(chip).toHaveAttribute('data-tone', 'busy');
    expect(within(row as HTMLElement).queryByRole('link', { name: /PR #118/ })).toBeNull();
  });

  it('opens on the running zone, and a task that finishes moves to the done tab', async () => {
    const { server } = await renderApp({ viewport: 'narrow' });
    expect(zoneTab(/^Running/)).toHaveAttribute('aria-selected', 'true');
    // NORT-9.1 is a todo with no agent, so it is waiting to start.
    expect(deckRows()).not.toContain('NORT-9.1');
    await userEvent.click(zoneTab(/^Not started/));
    expect(deckRows()).toContain('NORT-9.1');

    // The seed keeps done work off the desk, so a zone only fills as work
    // finishes on it. With no agent to finalise, done is done at once.
    server.change({ kind: 'task', ids: ['NORT-9.1'] }, (w) => {
      w.tasks['NORT-9.1'] = { ...w.tasks['NORT-9.1']!, status: 'done' };
    });
    await waitFor(() => expect(deckRows()).not.toContain('NORT-9.1'));

    await userEvent.click(zoneTab(/^Done/));
    expect(deckRows()).toContain('NORT-9.1');
  });

  it('names how much each zone holds, so a tab says what is behind it', async () => {
    await renderApp({ viewport: 'narrow' });
    // The seed puts six nodes in the running zone and none in done, which the
    // zone-membership test above pins by name.
    expect(zoneTab(/^Running/)).toHaveTextContent('6');
    expect(zoneTab(/^Done/)).toHaveTextContent('0');
  });

  it('says a zone is empty in its own words rather than showing nothing', async () => {
    const { server } = await renderApp({ viewport: 'narrow' });
    // Take every task off the desk, so the not-started zone draws nothing.
    server.change({ kind: 'desk', ids: [] }, (w) => {
      w.desk = {};
    });
    await userEvent.click(zoneTab(/^Not started/));
    await waitFor(() => expect(screen.getByTestId('deck-empty')).toHaveTextContent(/waiting/i));
  });

  it('opens a node full-screen from its row, and back returns to the deck', async () => {
    await renderApp({ viewport: 'narrow' });
    await userEvent.click(screen.getByRole('button', { name: /Migrate to Postgres 16/ }));
    expect(screen.getByRole('dialog')).toHaveTextContent('Migrate to Postgres 16');
    expect(screen.queryByTestId('deck-list')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByTestId('deck-list')).toBeInTheDocument();
  });

  it('pushes the session over the detail, and back pops one screen at a time', async () => {
    await renderApp({ viewport: 'narrow' });
    await userEvent.click(screen.getByRole('button', { name: /Migrate to Postgres 16/ }));
    await userEvent.click(screen.getByRole('link', { name: /Session/ }));
    expect(screen.getByTestId('session-tab')).toBeInTheDocument();
    // No tab strip in the narrow layout: one thing owns the screen.
    expect(screen.queryAllByRole('tab', { name: /session/i })).toHaveLength(0);

    await userEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('Migrate to Postgres 16');
    await userEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByTestId('deck-list')).toBeInTheDocument();
  });

  it('answers a waiting agent from the deck, so a checkpoint is clearable on a phone', async () => {
    const user = userEvent.setup();
    await renderApp({ viewport: 'narrow' });
    // MAEL-52 waits on a question in the seed.
    await user.click(screen.getByRole('button', { name: /Shape the orchestrator UI/ }));
    const card = screen.getByRole('dialog');
    const prompt = await within(card).findByTestId('question-prompt');
    expect(card).toHaveTextContent('Before this');
    await user.click(within(prompt).getAllByRole('radio')[0]!);
    await user.click(within(prompt).getByRole('button', { name: 'Answer' }));
    await waitFor(() => expect(nodeState('MAEL-52')).not.toBe('needs-attention'));
  });

  it('takes the attention chip to the node that needs the user, oldest ask first', async () => {
    await renderApp({ viewport: 'narrow' });
    await userEvent.click(screen.getByTestId('attention-chip'));
    // NORT-7 waits on a plan review in the seed, and is the oldest open ask.
    expect(screen.getByRole('dialog')).toHaveTextContent('Plan the order export');
  });

  it('opens the deck on the zone of the task the chip goes to', async () => {
    const user = userEvent.setup();
    await renderApp({ viewport: 'narrow' });
    // NORT-7 waits on a plan review, and its agent is running work.
    await user.click(screen.getByRole('tab', { name: /^Done/ }));
    await user.click(screen.getByTestId('attention-chip'));
    expect(await screen.findByRole('dialog')).toHaveTextContent('Plan the order export');
    // Back lands on a list that holds it, rather than the zone it was on.
    await user.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByRole('tab', { name: /^Running/ })).toHaveAttribute('aria-selected', 'true');
  });

  it('keeps the task list reachable, with its filters', async () => {
    await renderApp({ viewport: 'narrow' });
    await userEvent.click(screen.getByRole('button', { name: 'Task list' }));
    expect(screen.getByTestId('task-list')).toBeInTheDocument();
    expect(screen.queryByTestId('deck-list')).not.toBeInTheDocument();
  });

  it('still starts new work', async () => {
    await renderApp({ viewport: 'narrow' });
    await userEvent.click(screen.getByRole('button', { name: 'New' }));
    expect(screen.getByRole('dialog', { name: 'New work' })).toBeInTheDocument();
  });

  it('gives the document the full width, with no comment margin beside it', async () => {
    await renderApp({ viewport: 'narrow' });
    await userEvent.click(screen.getByRole('button', { name: /Plan the order export/ }));
    await userEvent.click(screen.getByRole('link', { name: /plan\.md/ }));
    expect(await screen.findByTestId('document-tab')).toBeInTheDocument();
    expect(screen.queryByTestId('comment-margin')).not.toBeInTheDocument();
  });

  it('leaves Enter as a newline and sends from the button, as a soft keyboard needs', async () => {
    const user = userEvent.setup();
    await renderApp({ viewport: 'narrow' });
    await user.click(screen.getByRole('button', { name: /Migrate to Postgres 16/ }));
    await user.click(screen.getByRole('link', { name: /Session/ }));
    const input = await screen.findByRole('textbox', { name: 'Message to agent' });
    // Enter makes a newline: it does not send, and it does not clear the box.
    await user.type(input, 'one{Enter}two');
    expect(input).toHaveValue('one\ntwo');

    await user.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByText('one two')).toBeInTheDocument();
    expect(input).toHaveValue('');
  });
});
