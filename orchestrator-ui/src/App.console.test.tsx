import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { commandsSince, exitAgent, paneItem } from './test/appHelpers';
import { renderApp } from './test/renderApp';

/** The strip at the foot of the body, or null when it is gone. */
const strip = () => screen.queryByRole('region', { name: 'Operations now' });
/** The strip's entries, one per operation, without its `+N more`. */
const stripEntries = () =>
  Array.from(strip()?.querySelectorAll<HTMLElement>('[data-operation-id]') ?? []);
/** The open console, or null. */
const consoleRegion = () => screen.queryByRole('region', { name: 'Console' });
const list = () => within(screen.getByRole('listbox', { name: 'Operations' }));
/** The ids of the listed operations, top to bottom. */
const listedIds = () =>
  list()
    .getAllByRole('option')
    .map((o) => o.getAttribute('data-operation-id'));
const log = () => within(screen.getByRole('region', { name: 'Operation log' }));

const ctrlBacktick = (target: Element = document.activeElement ?? document.body) =>
  fireEvent.keyDown(target, { key: '`', code: 'Backquote', ctrlKey: true });

afterEach(() => {
  vi.useRealTimers();
});

describe('the console strip', () => {
  it('is gone while nothing runs and nothing is unseen', async () => {
    await renderApp({ scenario: 'operations-history' });
    ctrlBacktick();
    // Sixty listed: the strip judged a loaded history, every one of it seen.
    await waitFor(() => expect(listedIds()).toHaveLength(60));
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(strip()).toBeNull();
  });

  it('leaves the top bar with no console control', async () => {
    await renderApp({ scenario: 'operations-live' });
    await waitFor(() => expect(stripEntries()).toHaveLength(3));
    const bar = within(screen.getByTestId('top-bar'));
    expect(bar.queryByRole('button', { name: /running|Console/ })).toBeNull();
  });

  it('says No operations yet in an empty console', async () => {
    await renderApp();
    ctrlBacktick();
    expect(screen.getByText('No operations yet.')).toBeInTheDocument();
  });

  it('shows what ended badly unseen, then what runs, with words, target and step', async () => {
    await renderApp({ scenario: 'operations-live' });
    await waitFor(() => expect(stripEntries()).toHaveLength(3));
    const entries = stripEntries();
    // Two unseen and three running: three fit, the history does not show.
    expect(entries.map((e) => e.getAttribute('data-operation-id'))).toEqual(['op4', 'op3', 'op7']);
    expect(strip()).toHaveTextContent('+2 more');
    expect(entries[0]).toHaveTextContent('Close refused');
    const restart = entries[2]!;
    expect(restart).toHaveTextContent('Restarting the environment of bravo');
    expect(restart).toHaveTextContent('northwind-bravo');
    expect(restart).toHaveTextContent('start_env');
  });

  it('keeps a done operation for four seconds, then lets it go', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const { server } = await renderApp();
    exitAgent(server, 'a1f3c9e2');
    await user.click(paneItem('Worktrees'));
    const row = () =>
      document.querySelector('[data-worktree-id="northwind-alpha"]') as HTMLElement | null;
    const close = within(row()!).getByRole('button', { name: 'Close' });
    await waitFor(() => expect(close).toBeEnabled());
    await user.click(close);

    const entry = await waitFor(() => within(strip()!).getByRole('button'));
    expect(entry).toHaveTextContent('Closing alpha');
    await waitFor(() => expect(server.world.operations['op1']?.state).toBe('done'));
    await waitFor(() =>
      expect(within(strip()!).getByRole('button')).toHaveTextContent('Closed alpha'),
    );
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(strip()).not.toBeNull();
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    await waitFor(() => expect(strip()).toBeNull());
  });

  it('opens the console on the entry that was clicked', async () => {
    const user = userEvent.setup();
    await renderApp({ scenario: 'operations-live' });
    await waitFor(() => expect(stripEntries()).toHaveLength(3));
    await user.click(stripEntries().find((e) => e.getAttribute('data-operation-id') === 'op7')!);
    expect(consoleRegion()).not.toBeNull();
    expect(list().getByRole('option', { selected: true })).toHaveAttribute(
      'data-operation-id',
      'op7',
    );
    expect(
      log().getByRole('heading', { name: 'Restarting the environment of bravo' }),
    ).toBeInTheDocument();
  });

  it('has room for two entries on the medium layout', async () => {
    await renderApp({ scenario: 'operations-live', viewport: 'medium' });
    await waitFor(() => expect(stripEntries()).toHaveLength(2));
    expect(strip()).toHaveTextContent('+3 more');
  });
});

describe('the console', () => {
  it('lists running and unseen first, then the rest, each newest first', async () => {
    await renderApp({ scenario: 'operations-live' });
    ctrlBacktick();
    await waitFor(() =>
      expect(listedIds()).toEqual(['op7', 'op6', 'op5', 'op4', 'op3', 'op2', 'op1']),
    );
  });

  it('filters to the running, the problems, or by text', async () => {
    const user = userEvent.setup();
    await renderApp({ scenario: 'operations-live' });
    ctrlBacktick();
    await waitFor(() => expect(listedIds()).toHaveLength(7));

    await user.click(screen.getByRole('button', { name: 'Running' }));
    expect(listedIds()).toEqual(['op7', 'op6', 'op5']);
    await user.click(screen.getByRole('button', { name: 'Problems' }));
    expect(listedIds()).toEqual(['op4', 'op3']);
    await user.click(screen.getByRole('button', { name: 'All' }));
    await user.type(screen.getByRole('searchbox', { name: 'Filter operations' }), 'delta');
    expect(listedIds()).toEqual(['op3', 'op2']);
  });

  it('logs the selected operation by step: done steps fold, the failing one opens', async () => {
    const user = userEvent.setup();
    await renderApp({ scenario: 'operations-live' });
    ctrlBacktick();
    await user.click(await list().findByRole('option', { name: /Close refused/ }));

    expect(log().getByRole('heading', { name: /Close refused/ })).toBeInTheDocument();
    expect(log().getByRole('link', { name: 'maelstrom-bravo' })).toBeInTheDocument();
    const stopEnv = log().getByRole('button', { name: /stop_env/ });
    expect(stopEnv).toHaveAttribute('aria-expanded', 'false');
    // The seed gives stop_env 1.7s.
    expect(stopEnv).toHaveTextContent('1.7s');
    expect(log().queryByText('Stopped the environment.')).toBeNull();
    expect(log().getByRole('button', { name: /git_close/ })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
    expect(
      await log().findByText('9a03d2f test: prune keeps uncovered projects', { exact: false }),
    ).toBeInTheDocument();

    await user.click(stopEnv);
    expect(log().getByText('Stopped the environment.')).toBeInTheDocument();
    // The arrows belong to the list: from a step they leave the selection be.
    await user.keyboard('{ArrowDown}');
    expect(list().getByRole('option', { selected: true })).toHaveAttribute(
      'data-operation-id',
      'op4',
    );
  });

  it('marks an operation seen once it is selected, and it leaves the strip', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp({ scenario: 'operations-live' });
    ctrlBacktick();
    const before = server.requests.length;
    await user.click(await list().findByRole('option', { name: /Merge failed/ }));
    await waitFor(() =>
      expect(commandsSince(server, before)).toEqual(['POST /api/operations/op3/seen']),
    );
    await user.keyboard('{Escape}');
    await waitFor(() =>
      expect(stripEntries().map((e) => e.getAttribute('data-operation-id'))).toEqual([
        'op4',
        'op7',
        'op6',
      ]),
    );
  });

  it('holds the list still while an operation it pinned is seen', async () => {
    const user = userEvent.setup();
    await renderApp({ scenario: 'operations-live' });
    ctrlBacktick();
    await user.click(await list().findByRole('option', { name: /Close refused/ }));
    await waitFor(() =>
      expect(list().getByRole('option', { name: /Close refused/ })).not.toHaveAttribute(
        'data-unseen',
      ),
    );
    expect(listedIds()).toEqual(['op7', 'op6', 'op5', 'op4', 'op3', 'op2', 'op1']);
  });

  it('offers the next chains of a refused close, with their confirms', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp({ scenario: 'operations-live' });
    ctrlBacktick();
    await user.click(await list().findByRole('option', { name: /Close refused/ }));

    expect(log().getByRole('button', { name: 'Trash' })).toBeInTheDocument();
    expect(log().getByRole('button', { name: 'Delete' })).toBeInTheDocument();
    const before = server.requests.length;
    await user.click(log().getByRole('button', { name: 'Shelve' }));
    expect(screen.getByText(/Shelve bravo\?/)).toBeInTheDocument();
    await user.click(
      within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Shelve it' }),
    );
    // The chain ends with the shelve: the console takes nothing off the desk.
    const shelve = () => Object.values(server.world.operations).find((o) => o.kind === 'shelve');
    await waitFor(() => expect(shelve()?.state).toBe('done'));
    expect(commandsSince(server, before)).toEqual([
      'POST /api/worktrees/maelstrom-bravo/force-close',
    ]);
  });

  it('closes the drawer on its × button, and the strip comes back', async () => {
    const user = userEvent.setup();
    await renderApp({ scenario: 'operations-live' });
    await waitFor(() => expect(stripEntries()).toHaveLength(3));
    await user.click(stripEntries()[0]!);
    expect(strip()).toBeNull();
    await user.click(within(consoleRegion()!).getByRole('button', { name: 'Close console' }));
    expect(consoleRegion()).toBeNull();
    expect(stripEntries()).toHaveLength(3);
  });

  it('lists a long history whole, newest first', async () => {
    await renderApp({ scenario: 'operations-history' });
    ctrlBacktick();
    await waitFor(() => expect(listedIds()).toHaveLength(60));
    expect(listedIds()[0]).toBe('op60');
  });
});

describe('the console keys', () => {
  it('opens on Ctrl-` from anywhere but a text field, and Esc closes it', async () => {
    const user = userEvent.setup();
    await renderApp({ scenario: 'operations-live' });
    ctrlBacktick();
    expect(consoleRegion()).not.toBeNull();
    ctrlBacktick();
    expect(consoleRegion()).toBeNull();
    ctrlBacktick();
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(consoleRegion()).toBeNull();

    await user.click(paneItem('Tasks'));
    const field = screen.getAllByRole('searchbox')[0]!;
    field.focus();
    ctrlBacktick(field);
    expect(consoleRegion()).toBeNull();
  });

  it('returns focus to where it was when it closes', async () => {
    const user = userEvent.setup();
    await renderApp({ scenario: 'operations-live' });
    const desk = paneItem('Desk');
    desk.focus();
    ctrlBacktick(desk);
    expect(consoleRegion()).not.toBeNull();
    expect(document.activeElement).not.toBe(desk);
    await user.keyboard('{Escape}');
    expect(consoleRegion()).toBeNull();
    expect(document.activeElement).toBe(desk);
  });

  it('moves through the list with the arrows, and Enter opens the target', async () => {
    const user = userEvent.setup();
    const { router, server } = await renderApp({ scenario: 'operations-live' });
    ctrlBacktick();
    await waitFor(() =>
      expect(list().getByRole('option', { selected: true })).toHaveAttribute(
        'data-operation-id',
        'op7',
      ),
    );
    // R does nothing to an operation that still runs.
    const before = server.requests.length;
    await user.keyboard('r');
    expect(commandsSince(server, before)).toEqual([]);
    await user.keyboard('{ArrowDown}{ArrowDown}');
    expect(list().getByRole('option', { selected: true })).toHaveAttribute(
      'data-operation-id',
      'op5',
    );
    await user.keyboard('{ArrowUp}');
    expect(list().getByRole('option', { selected: true })).toHaveAttribute(
      'data-operation-id',
      'op6',
    );
    await user.keyboard('{Enter}');
    expect(router.state.location.pathname).toBe('/desk/worktree/northwind-alpha');
    expect(consoleRegion()).toBeNull();
  });

  it('retries a refused operation on R', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    exitAgent(server, 'b7d2e4a0');
    await user.click(paneItem('Worktrees'));
    // maelstrom-alpha has 3 dirty files, which the close refuses.
    const row = document.querySelector('[data-worktree-id="maelstrom-alpha"]') as HTMLElement;
    const close = within(row).getByRole('button', { name: 'Close' });
    await waitFor(() => expect(close).toBeEnabled());
    await user.click(close);
    await waitFor(() => expect(server.world.operations['op1']?.state).toBe('refused'));

    ctrlBacktick();
    // The console reads the refusal from the list, which can trail the world.
    await waitFor(() =>
      expect(list().getByRole('option', { selected: true })).toHaveAttribute(
        'data-state',
        'refused',
      ),
    );
    const before = server.requests.length;
    await user.keyboard('r');
    await waitFor(() =>
      expect(commandsSince(server, before)).toContain('POST /api/operations/op1/retry'),
    );
  });

  it('says why a retry failed', async () => {
    const user = userEvent.setup();
    await renderApp({ scenario: 'operations-live' });
    ctrlBacktick();
    await user.click(await list().findByRole('option', { name: /Close refused/ }));
    // The fake never ran the seeded close, so it cannot run it again.
    await user.click(log().getByRole('button', { name: 'Retry' }));
    expect(await log().findByRole('alert')).toHaveTextContent(/^Retry failed: .*op4/);
  });
});

/**
 * Give each strip and drawer a long CSS transition, which ends when the test
 * says so. Without it, jsdom computes no transition and every change is instant.
 */
function stubTransitions() {
  const original = window.getComputedStyle;
  window.getComputedStyle = (el, pseudo) => {
    const style = original(el, pseudo);
    if (!(el instanceof HTMLElement) || !el.dataset.presence) return style;
    return new Proxy(style, {
      get: (target, prop) => {
        if (prop === 'transitionDuration') return '10s';
        const value: unknown = Reflect.get(target, prop);
        return typeof value === 'function' ? value.bind(target) : value;
      },
    });
  };
  /** End the transition on `el`. */
  const end = (el: Element) => act(async () => void fireEvent.transitionEnd(el));
  return {
    end,
    /** End every exit now playing. */
    endExits: async () => {
      for (const el of document.querySelectorAll('[data-presence="out"]')) await end(el);
    },
    restore: () => {
      window.getComputedStyle = original;
    },
  };
}

describe('the console in motion', () => {
  let motion: ReturnType<typeof stubTransitions>;
  beforeEach(() => {
    motion = stubTransitions();
  });
  afterEach(() => motion.restore());

  it('keeps the drawer, inert, until its exit ends, then brings the strip back', async () => {
    const user = userEvent.setup();
    await renderApp({ scenario: 'operations-live' });
    await waitFor(() => expect(stripEntries()).toHaveLength(3));
    await user.click(stripEntries()[0]!);
    // The strip goes at once: the drawer rises over the place it held.
    expect(strip()).toBeNull();

    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(consoleRegion()).toHaveAttribute('inert');
    expect(strip()).toBeNull();

    await motion.endExits();
    expect(consoleRegion()).toBeNull();
    expect(stripEntries()).toHaveLength(3);
  });

  it('keeps the drawer when Ctrl-` opens it again during its exit', async () => {
    await renderApp({ scenario: 'operations-live' });
    ctrlBacktick();
    const drawer = consoleRegion();
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    ctrlBacktick();
    // The transition turns back to rest, and its end keeps the same drawer.
    await motion.end(drawer!);
    expect(consoleRegion()).toBe(drawer);
    expect(consoleRegion()).not.toHaveAttribute('inert');
  });

  it('gives focus back as the exit starts, not as it ends', async () => {
    await renderApp({ scenario: 'operations-live' });
    const desk = paneItem('Desk');
    desk.focus();
    ctrlBacktick(desk);
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(consoleRegion()).not.toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(desk));
  });

  it('keeps its last entry, inert, while the strip sinks out', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const { server } = await renderApp();
    exitAgent(server, 'a1f3c9e2');
    await user.click(paneItem('Worktrees'));
    const row = () =>
      document.querySelector('[data-worktree-id="northwind-alpha"]') as HTMLElement | null;
    const close = within(row()!).getByRole('button', { name: 'Close' });
    await waitFor(() => expect(close).toBeEnabled());
    await user.click(close);
    await waitFor(() =>
      expect(within(strip()!).getByRole('button')).toHaveTextContent('Closed alpha'),
    );

    // The done operation's 4 seconds run out: the strip sinks with it still shown.
    act(() => {
      vi.advanceTimersByTime(4500);
    });
    expect(strip()).toHaveAttribute('inert');
    expect(strip()).toHaveTextContent('Closed alpha');
    await motion.endExits();
    expect(strip()).toBeNull();
  });
});

describe('the console on a phone', () => {
  it('shows one entry and a count in the strip, and opens as a pushed screen', async () => {
    const user = userEvent.setup();
    await renderApp({ scenario: 'operations-live', viewport: 'narrow' });
    await waitFor(() => expect(stripEntries()).toHaveLength(1));
    const entries = stripEntries();
    expect(strip()).toHaveTextContent('+4 more');

    await user.click(entries[0]!);
    expect(consoleRegion()).not.toBeNull();
    expect(screen.getByTestId('screen-title')).toHaveTextContent('Console');
    // The screen strip has the Close; the drawer's × is not shown.
    expect(within(consoleRegion()!).queryByRole('button', { name: 'Close console' })).toBeNull();
    await user.click(within(screen.getByTestId('top-bar')).getByRole('button', { name: 'Close' }));
    expect(consoleRegion()).toBeNull();
  });
});
