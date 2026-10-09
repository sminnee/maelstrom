import { describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { act } from 'react';
import userEvent from '@testing-library/user-event';
import {
  commandsSince,
  nodeState,
  openSheet,
  screenStrip,
  paneItem,
  touchDrag,
} from './test/appHelpers';
import { renderApp } from './test/renderApp';
import type { FakeServer } from './fake/fakeServer';

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
    expect(screen.queryByRole('button', { name: 'Tabs' })).toBeNull();
  });

  it('reads the PR number on a deck row, as the canvas node does', async () => {
    await renderApp({ viewport: 'narrow' });
    const row = screen.getByTestId('deck-list').querySelector('[data-task-id="NORT-12"]');
    expect(row).toHaveTextContent('#118');
    // The chip carries its state in words here too, but not as a link: the
    // whole row is a link, and an anchor may not nest inside one.
    const chip = within(row as HTMLElement).getByLabelText('PR #118, CI running');
    expect(chip).toHaveAttribute('data-tone', 'busy');
    expect(chip.tagName).not.toBe('A');
    expect(chip.closest('a')).toBe(row?.querySelector('a'));
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

  it('opens on the zone the URL names, and a tab moves the location', async () => {
    const { router } = await renderApp({ viewport: 'narrow', url: '/desk?zone=done' });
    expect(zoneTab(/^Done/)).toHaveAttribute('aria-selected', 'true');
    await userEvent.click(zoneTab(/^Running/));
    expect(router.state.location.search).toBe('');
    expect(router.state.historyAction).toBe('PUSH');
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

  it('opens a node full-screen from its row, and the screen strip replaces the top bar until it returns', async () => {
    await renderApp({ viewport: 'narrow' });
    const bar = () => within(screen.getByTestId('top-bar'));
    expect(bar().getByRole('group', { name: 'Views' })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('link', { name: /Migrate to Postgres 16/ }));
    expect(screen.getByRole('dialog')).toHaveTextContent('Migrate to Postgres 16');
    expect(screen.queryByTestId('deck-list')).not.toBeInTheDocument();
    // One strip: the way back, what the screen is, and More. The readings and
    // New move into the side sheet.
    expect(bar().queryByRole('group', { name: 'Views' })).toBeNull();
    expect(bar().getByTestId('screen-title')).toHaveTextContent('Migrate to Postgres 16');
    expect(bar().queryByRole('button', { name: 'New' })).toBeNull();

    await userEvent.click(bar().getByRole('button', { name: 'Back' }));
    expect(screen.getByTestId('deck-list')).toBeInTheDocument();
    expect(bar().getByRole('group', { name: 'Views' })).toBeInTheDocument();
    expect(bar().queryByRole('button', { name: 'Back' })).toBeNull();
  });

  it('opens the side sheet from More, with New in it, which closes it', async () => {
    const user = userEvent.setup();
    await renderApp({ viewport: 'narrow' });
    await user.click(screen.getByRole('link', { name: /Migrate to Postgres 16/ }));
    const more = screenStrip().getByRole('button', { name: 'More' });
    expect(more).toHaveAttribute('aria-expanded', 'false');

    await user.click(more);
    expect(more).toHaveAttribute('aria-expanded', 'true');
    const sheet = screen.getByRole('dialog', { name: 'More' });
    await user.click(within(sheet).getByRole('button', { name: 'New' }));
    expect(screen.queryByRole('dialog', { name: 'More' })).toBeNull();
    expect(screen.getByRole('dialog', { name: 'New work' })).toBeInTheDocument();
  });

  it('draws the attention chip on a pushed screen only while something waits, and on the deck always', async () => {
    const { server } = await renderApp({ viewport: 'narrow' });
    await userEvent.click(screen.getByRole('link', { name: /Migrate to Postgres 16/ }));
    const bar = screenStrip();
    expect(bar.getByTestId('attention-chip')).toBeInTheDocument();
    act(() => {
      server.change({ kind: 'attention', ids: Object.keys(server.world.attention) }, (w) => {
        w.attention = {};
        for (const agent of Object.values(w.agents)) agent.pendingRequestIds = [];
      });
    });
    await waitFor(() => expect(bar.queryByTestId('attention-chip')).toBeNull());
    // The deck's bar keeps the chip at 0: it is where the chip lives.
    await userEvent.click(bar.getByRole('button', { name: 'Back' }));
    expect(screenStrip().getByTestId('attention-chip')).toHaveAttribute('data-count', '0');
  });

  it('opens on the detail the URL names, and Back with nothing behind it goes to the deck', async () => {
    const { router } = await renderApp({ viewport: 'narrow', url: '/desk/task/NORT-9' });
    expect(screen.getByRole('dialog')).toHaveTextContent('Migrate to Postgres 16');
    await userEvent.click(screenStrip().getByRole('button', { name: 'Back' }));
    expect(router.state.location.pathname).toBe('/desk');
    expect(router.state.historyAction).toBe('REPLACE');
    expect(screen.getByTestId('deck-list')).toBeInTheDocument();
  });

  it('links each row to its card', async () => {
    await renderApp({ viewport: 'narrow' });
    expect(screen.getByRole('link', { name: /Migrate to Postgres 16/ })).toHaveAttribute(
      'href',
      '/desk/task/NORT-9',
    );
  });

  it('puts the status control on the id line of the detail', async () => {
    const user = userEvent.setup();
    await renderApp({ viewport: 'narrow' });
    await user.click(zoneTab(/not started/i));
    await user.click(screen.getByRole('link', { name: /Watch the migration PR/ }));
    expect(
      within(screen.getByTestId('node-id-line')).getByRole('button', {
        name: 'Status of Watch the migration PR, todo',
      }),
    ).toBeInTheDocument();
  });

  it('pushes the session over the detail, and back pops one screen at a time', async () => {
    const { router } = await renderApp({ viewport: 'narrow' });
    await userEvent.click(screen.getByRole('link', { name: /Migrate to Postgres 16/ }));
    await userEvent.click(screen.getByRole('link', { name: /Session/ }));
    expect(screen.getByTestId('session-tab')).toBeInTheDocument();
    // No tab strip in the narrow layout: one thing owns the screen.
    expect(screen.queryAllByRole('tab', { name: /session/i })).toHaveLength(0);

    await userEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(await screen.findByRole('dialog')).toHaveTextContent('Migrate to Postgres 16');
    // The browser's Back: the closed screen is ahead in history, not behind.
    expect(router.state.historyAction).toBe('POP');
    await userEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(await screen.findByTestId('deck-list')).toBeInTheDocument();
    expect(router.state.historyAction).toBe('POP');
  });

  it('puts Stop in the session strip, and the head in the side sheet', async () => {
    const user = userEvent.setup();
    await renderApp({ viewport: 'narrow' });
    await user.click(screen.getByRole('link', { name: /Migrate to Postgres 16/ }));
    await user.click(screen.getByRole('link', { name: /Session/ }));
    const tab = await screen.findByTestId('session-tab');
    const bar = screenStrip();
    expect(bar.getByTestId('screen-title')).toHaveTextContent('d9a4c7f1');
    expect(bar.getByRole('button', { name: 'Stop' })).toBeInTheDocument();
    // The body is the transcript and the input.
    expect(within(tab).queryByTestId('session-head')).toBeNull();
    expect(within(tab).queryByRole('button', { name: 'Compact' })).toBeNull();
    expect(within(tab).getByRole('textbox', { name: 'Message to agent' })).toBeInTheDocument();

    const sheet = await openSheet(user);
    expect(sheet.getByRole('button', { name: 'Compact' })).toBeInTheDocument();
    expect(sheet.getByTitle(/^Permission mode/)).toBeInTheDocument();
    // The node detail under it carries the end-of-work control, so the session does not.
    expect(sheet.queryByRole('button', { name: 'Dismiss' })).toBeNull();
  });

  it('answers a waiting agent from the deck, so a checkpoint is clearable on a phone', async () => {
    const user = userEvent.setup();
    await renderApp({ viewport: 'narrow' });
    // MAEL-52 waits on a question in the seed.
    await user.click(screen.getByRole('link', { name: /Shape the orchestrator UI/ }));
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

  it('takes the attention chip to a free agent that waits on the user', async () => {
    const { server } = await renderApp({ viewport: 'narrow' });
    act(() => {
      server.change({ kind: 'attention', ids: ['att-free-plan'] }, (w) => {
        w.attention['att-free-plan'] = {
          id: 'att-free-plan',
          kind: 'plan_review',
          agentId: 'f2c6a9d4',
          taskId: null,
          documentId: null,
          requestId: 'req-free-plan',
          summary: 'Plan awaiting review',
          // Older than NORT-7's plan review, so it ranks first.
          raisedAt: '2000-01-01T00:00:00Z',
          clearedAt: null,
        };
      });
    });
    await waitFor(() =>
      expect(screen.getByTestId('attention-chip')).toHaveAttribute('data-count', '4'),
    );
    await userEvent.click(screen.getByTestId('attention-chip'));
    expect(screen.getByRole('dialog')).toHaveAccessibleName('bravo · feat/task-index');
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

  it('says when the agent host stopped answering, as the wider layouts do', async () => {
    await renderApp({ viewport: 'narrow', scenario: 'host-down' });
    expect(await screen.findByRole('status')).toHaveTextContent('Agent host unreachable since');
  });

  describe('the Filters side sheet', () => {
    const filtersButton = () => screen.getByRole('button', { name: /^Filters/ });
    const sheet = () => within(screen.getByRole('dialog', { name: 'Filters' }));
    const sheetOpen = () => screen.queryByRole('dialog', { name: 'Filters' }) !== null;

    it('holds the Desk filters, which filter the deck behind it', async () => {
      const user = userEvent.setup();
      await renderApp({ viewport: 'narrow' });
      expect(screen.queryByLabelText('Project')).toBeNull();
      expect(filtersButton()).toHaveAttribute('aria-expanded', 'false');

      await user.click(filtersButton());
      expect(filtersButton()).toHaveAttribute('aria-expanded', 'true');
      for (const label of ['Project', 'Branch', 'Agent status', 'Search'])
        expect(sheet().getByLabelText(label)).toBeInTheDocument();
      expect(sheet().queryByRole('button', { name: /^Status/ })).toBeNull();

      await user.type(sheet().getByLabelText('Search'), 'order export');
      await waitFor(() => expect(deckRows()).toEqual(['NORT-7']));
      expect(sheetOpen()).toBe(true);
    });

    it('counts the filters that narrow the view, so none is hidden', async () => {
      const user = userEvent.setup();
      await renderApp({ viewport: 'narrow' });
      expect(filtersButton()).toHaveAccessibleName('Filters');
      await user.click(filtersButton());
      await user.selectOptions(sheet().getByLabelText('Project'), 'northwind');
      await user.selectOptions(sheet().getByLabelText('Agent status'), 'planned');
      expect(filtersButton()).toHaveAccessibleName('Filters · 2');
    });

    it('closes on its Close button, and on a tap on the backdrop', async () => {
      const user = userEvent.setup();
      await renderApp({ viewport: 'narrow' });
      await user.click(filtersButton());
      // The bare cross leads the head row, where a sheet's close sits.
      const [first] = sheet().getAllByRole('button');
      expect(first).toHaveAccessibleName('Close');
      expect(first).toHaveTextContent('');
      await user.click(first!);
      expect(sheetOpen()).toBe(false);

      await user.click(filtersButton());
      // jsdom draws the box at 0×0, so any point is on the backdrop.
      const box = screen.getByRole('dialog', { name: 'Filters' });
      fireEvent.pointerDown(box, { clientX: 20, clientY: 20 });
      fireEvent.click(box, { clientX: 20, clientY: 20 });
      expect(sheetOpen()).toBe(false);
    });

    it('keeps a search typed just before the sheet closes', async () => {
      const user = userEvent.setup();
      await renderApp({ viewport: 'narrow' });
      await user.click(filtersButton());
      await user.type(sheet().getByLabelText('Search'), 'order export');
      await user.click(sheet().getByRole('button', { name: 'Close' }));
      expect(deckRows()).toEqual(['NORT-7']);
    });

    it('holds the Tasks filters on Tasks', async () => {
      const user = userEvent.setup();
      await renderApp({ viewport: 'narrow' });
      await user.click(paneItem('Tasks'));
      expect(screen.getByTestId('task-list')).toBeInTheDocument();
      expect(screen.queryByTestId('deck-list')).not.toBeInTheDocument();
      await user.click(filtersButton());
      expect(sheet().getByRole('button', { name: /^Status/ })).toBeInTheDocument();
      expect(sheet().getByLabelText('Search')).toBeInTheDocument();
      expect(sheet().queryByLabelText('Agent status')).toBeNull();
    });
  });

  it("pushes the agent's session from a task's state link, with no editor", async () => {
    await renderApp({ viewport: 'narrow' });
    await userEvent.click(paneItem('Tasks'));
    const row = screen.getByTestId('task-list').querySelector('[data-task-id="NORT-7"]');
    await userEvent.click(within(row as HTMLElement).getByRole('link', { name: /needs you/i }));
    expect(await screen.findByTestId('session-tab')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('still starts new work', async () => {
    await renderApp({ viewport: 'narrow' });
    await userEvent.click(screen.getByRole('button', { name: 'New' }));
    expect(screen.getByRole('dialog', { name: 'New work' })).toBeInTheDocument();
  });

  it('gives the document the full width, with no comment margin beside it', async () => {
    await renderApp({ viewport: 'narrow' });
    await userEvent.click(screen.getByRole('link', { name: /Plan the order export/ }));
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
    await user.click(screen.getByRole('link', { name: /Migrate to Postgres 16/ }));
    await user.click(screen.getByRole('link', { name: /Session/ }));
    const input = await screen.findByRole('textbox', { name: 'Message to agent' });
    // Enter makes a newline: it does not send, and it does not clear the box.
    await user.type(input, 'one{Enter}two');
    expect(input).toHaveValue('one\ntwo');

    await user.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByText('one two')).toBeInTheDocument();
    expect(input).toHaveValue('');
  });

  describe('a swipe on a deck row', () => {
    const row = (id: string) =>
      screen.getByTestId('deck-list').querySelector<HTMLElement>(`[data-task-id="${id}"]`)!;
    const sliding = (id: string) => row(id).querySelector<HTMLElement>('a')!;

    const reveal = (id: string) => within(row(id)).getByTestId('swipe-reveal');
    const drag = (el: HTMLElement, dx: number, dy = 0, { release = true } = {}) =>
      touchDrag(el, { from: { x: 300, y: 40 }, by: { x: dx, y: dy }, release });

    /** NORT-9.1 follows NORT-9, so it waits, and has no swipe. Let it start. */
    async function readyToLaunch() {
      const app = await renderApp({ viewport: 'narrow', url: '/desk?zone=notStarted' });
      expect(row('NORT-9.1')).not.toHaveAttribute('data-swipe');
      app.server.change({ kind: 'task', ids: ['NORT-9.1'] }, (w) => {
        w.tasks['NORT-9.1'] = { ...w.tasks['NORT-9.1']!, actionable: true };
      });
      await waitFor(() => expect(row('NORT-9.1')).toHaveAttribute('data-swipe', 'launch'));
      return app;
    }

    it('launches a not-started row dragged past 35% of its width', async () => {
      const { server } = await readyToLaunch();
      // A 400px row: the threshold is 140px.
      vi.spyOn(sliding('NORT-9.1'), 'getBoundingClientRect').mockReturnValue(
        DOMRect.fromRect({ width: 400, height: 80 }),
      );
      const before = server.requests.length;
      drag(sliding('NORT-9.1'), -120);
      expect(reveal('NORT-9.1')).toHaveAttribute('data-armed', 'false');
      expect(commandsSince(server, before)).toEqual([]);
      drag(sliding('NORT-9.1'), -150);
      await waitFor(() =>
        expect(commandsSince(server, before)).toEqual(['POST /api/tasks/NORT-9.1/launch']),
      );
    });

    it('springs back, and sends nothing, on a release before the threshold', async () => {
      const { server } = await readyToLaunch();
      const before = server.requests.length;
      drag(sliding('NORT-9.1'), -50);
      expect(sliding('NORT-9.1').style.transform).toBe('');
      expect(reveal('NORT-9.1')).toHaveAttribute('data-armed', 'false');
      // A drag right uncovers nothing: the row only moves left.
      drag(sliding('NORT-9.1'), 100, 0, { release: false });
      expect(sliding('NORT-9.1').style.transform).toBe('');
      expect(commandsSince(server, before)).toEqual([]);
    });

    it('opens the row its whole width when the launch refuses, with the reason in the reveal', async () => {
      const { server } = await readyToLaunch();
      vi.spyOn(sliding('NORT-9.1'), 'getBoundingClientRect').mockReturnValue(
        DOMRect.fromRect({ width: 400, height: 80 }),
      );
      server.refuse(/POST \/api\/tasks\/NORT-9.1\/launch$/, {
        status: 409,
        code: 'invalid',
        message: 'No free worktree',
      });
      drag(sliding('NORT-9.1'), -150);
      await waitFor(() => expect(reveal('NORT-9.1')).toHaveTextContent('No free worktree'));
      expect(reveal('NORT-9.1')).toHaveAttribute('data-armed', 'true');
      expect(sliding('NORT-9.1').style.transform).toBe('translateX(-400px)');
      // A second swipe while it shows sends nothing.
      const before = server.requests.length;
      drag(sliding('NORT-9.1'), -100);
      expect(commandsSince(server, before)).toEqual([]);
    });

    it('arms the reveal at the threshold with one haptic tick, and disarms going back', async () => {
      const vibrate = vi.fn();
      vi.stubGlobal('navigator', Object.assign(Object.create(navigator), { vibrate }));
      try {
        await readyToLaunch();
        const armed = () => reveal('NORT-9.1');
        drag(sliding('NORT-9.1'), -50, 0, { release: false });
        expect(armed()).toHaveAttribute('data-armed', 'false');
        expect(sliding('NORT-9.1').style.transform).toBe('translateX(-50px)');
        fireEvent.pointerMove(sliding('NORT-9.1'), { pointerId: 1, clientX: 200, clientY: 40 });
        fireEvent.pointerMove(sliding('NORT-9.1'), { pointerId: 1, clientX: 190, clientY: 40 });
        expect(armed()).toHaveAttribute('data-armed', 'true');
        expect(vibrate).toHaveBeenCalledTimes(1);
        fireEvent.pointerMove(sliding('NORT-9.1'), { pointerId: 1, clientX: 260, clientY: 40 });
        expect(armed()).toHaveAttribute('data-armed', 'false');
        expect(vibrate).toHaveBeenCalledTimes(1);
        fireEvent.pointerCancel(sliding('NORT-9.1'), { pointerId: 1 });
        expect(sliding('NORT-9.1').style.transform).toBe('');
      } finally {
        vi.unstubAllGlobals();
      }
    });

    it('dismisses a done row, even one whose task reads as actionable', async () => {
      const { server } = await renderApp({ viewport: 'narrow', url: '/desk?zone=done' });
      // Only a not-started row launches: the zone decides, not the flag.
      server.change({ kind: 'task', ids: ['NORT-9.1'] }, (w) => {
        w.tasks['NORT-9.1'] = { ...w.tasks['NORT-9.1']!, status: 'done', actionable: true };
      });
      await waitFor(() => expect(row('NORT-9.1')).toHaveAttribute('data-swipe', 'dismiss'));
      const before = server.requests.length;
      drag(sliding('NORT-9.1'), -100);
      await waitFor(() => expect(deckRows()).not.toContain('NORT-9.1'));
      expect(commandsSince(server, before)).toEqual(['DELETE /api/desk/task:NORT-9.1']);
    });

    it('dismisses an exited row with the chain the card runs, closing a worktree it is alone in', async () => {
      const { server } = await renderApp({ viewport: 'narrow' });
      server.change({ kind: 'agent', ids: ['a1f3c9e2'] }, (w) => {
        w.agents['a1f3c9e2'] = {
          ...w.agents['a1f3c9e2']!,
          state: 'exited',
          exitCode: 1,
          pendingRequestIds: [],
        };
      });
      await waitFor(() => expect(nodeState('NORT-7')).toBe('exited'));
      expect(row('NORT-7')).toHaveAttribute('data-swipe', 'dismiss');
      const before = server.requests.length;
      drag(sliding('NORT-7'), -100);
      await waitFor(() => expect(deckRows()).not.toContain('NORT-7'));
      expect(commandsSince(server, before)).toEqual([
        'POST /api/worktrees/northwind-alpha/close',
        'DELETE /api/desk/task:NORT-7',
      ]);
    });

    it('gives a working row no swipe', async () => {
      await renderApp({ viewport: 'narrow' });
      const working = screen
        .getByTestId('deck-list')
        .querySelector<HTMLElement>('[data-testid="deck-row"][data-state="working"]')!;
      expect(working).not.toHaveAttribute('data-swipe');
      expect(within(working).queryByTestId('swipe-reveal')).toBeNull();
    });

    it('lets a vertical drag go, so the list scrolls', async () => {
      const { server } = await readyToLaunch();
      const before = server.requests.length;
      drag(sliding('NORT-9.1'), -100, 150);
      expect(sliding('NORT-9.1').style.transform).toBe('');
      expect(commandsSince(server, before)).toEqual([]);
    });

    it('does not open the node from the click that ends a swipe', async () => {
      const { router } = await readyToLaunch();
      const path = router.state.location.pathname;
      drag(sliding('NORT-9.1'), -50);
      fireEvent.click(sliding('NORT-9.1'));
      expect(router.state.location.pathname).toBe(path);
      // The next tap opens it.
      fireEvent.pointerDown(sliding('NORT-9.1'), { pointerId: 2, isPrimary: true });
      fireEvent.click(sliding('NORT-9.1'));
      expect(router.state.location.pathname).not.toBe(path);
    });
  });
});
