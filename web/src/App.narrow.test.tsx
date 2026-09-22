import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import { act } from 'react';
import userEvent from '@testing-library/user-event';
import { nodeState } from './test/appHelpers';
import { renderApp } from './test/renderApp';
import type { FakeServer } from './test/fakeServer';

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

  it('keeps Tasks reachable while hiding desktop filters', async () => {
    await renderApp({ viewport: 'narrow' });
    await userEvent.click(screen.getByRole('button', { name: 'Tasks' }));
    expect(screen.getByTestId('task-list')).toBeInTheDocument();
    expect(screen.queryByTestId('deck-list')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Project')).toBeNull();
    expect(screen.queryByLabelText('Branch')).toBeNull();
  });

  it('still starts new work', async () => {
    await renderApp({ viewport: 'narrow' });
    await userEvent.click(screen.getByRole('button', { name: 'New' }));
    expect(screen.getByRole('dialog', { name: 'New work' })).toBeInTheDocument();
  });

  it('gives the document the full width, with no comment margin beside it', async () => {
    await renderApp({ viewport: 'narrow' });
    await userEvent.click(screen.getByRole('button', { name: /Plan the order export/ }));
    await userEvent.click(screen.getByRole('link', { name: /Plan/ }));
    expect(await screen.findByTestId('document-tab')).toBeInTheDocument();
    expect(screen.queryByTestId('comment-margin')).not.toBeInTheDocument();
  });

  /**
   * Put a five-hour reading on the host. `spent` is the utilisation and
   * `hoursLeft` how much of the window remains. With `week` a seven-day window
   * is reported too, at 30% one day in — `busy`, so its chip survives the
   * narrow bar and a case can wait on it.
   *
   * Times are built from `Date.now()` so pace does not drift with the wall clock.
   */
  const reportUsage = async (
    server: FakeServer,
    { spent, hoursLeft, week }: { spent: number; hoursLeft: number; week?: boolean },
  ) => {
    await act(async () => {
      server.change({ kind: 'host', ids: ['agent-host'] }, (world) => {
        world.host = {
          ...world.host!,
          usage: {
            fiveHour: {
              utilization: spent,
              resetsAt: Math.floor(Date.now() / 1000) + hoursLeft * 3600,
            },
            sevenDay: week
              ? { utilization: 0.3, resetsAt: Math.floor(Date.now() / 1000) + 6 * 86_400 }
              : null,
            at: new Date().toISOString(),
          },
        };
      });
    });
  };

  // `isNotable` in `selectors/usage.ts` owns which readings are worth a band,
  // and `usage.test.ts` pins its cases. These two say the narrow bar applies
  // it: one reading through, one held back.
  it('shows a window that is ahead of pace, which is worth a band on a phone', async () => {
    const { server } = await renderApp({ viewport: 'narrow' });
    // 45% spent with three of five hours left: amber, the same figure
    // `usage.test.ts` pins as `busy`.
    await reportUsage(server, { spent: 0.45, hoursLeft: 3 });
    await waitFor(() =>
      expect(screen.getByLabelText(/5-hour limit: 45% consumed/)).toBeInTheDocument(),
    );
  });

  it('withholds a window that is keeping up, so a quiet bar stays one row', async () => {
    const { server } = await renderApp({ viewport: 'narrow' });
    // 20% spent with two of five hours left: a quotient of 0.5, well inside the
    // quiet band rather than balanced on the pace line.
    await reportUsage(server, { spent: 0.2, hoursLeft: 2, week: true });
    // The week chip survives the narrow bar, so waiting on it proves the
    // reading landed before the absence below is read.
    await screen.findByLabelText(/7-day limit: 30% consumed/);
    expect(screen.queryByLabelText(/5-hour limit/)).toBeNull();
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
