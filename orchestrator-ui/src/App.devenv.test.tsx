import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent, { type UserEvent } from '@testing-library/user-event';
import {
  expanded,
  openSession,
  stripKeys,
  tabBody,
  tabStrip,
  worktreeRow,
} from './test/appHelpers';
import { clickNode, renderApp } from './test/renderApp';

/** NORT-12's agent runs in delta, where `web` runs and `ladle` is stopped. */
const WEB_TAB = 'devenv:northwind-delta:web';
const DELTA_SESSION = 'session:e5b1d8c3';
const DELTA_CHANGES = 'changes:northwind-delta';

/** Press a link on NORT-12's card, which must be open. */
const fromCard = (user: UserEvent, name: string) =>
  user.click(within(expanded()).getByRole('link', { name }));

/** Open NORT-12's card, then its session and delta's `web`: web is active. */
async function openSessionAndWeb(user: UserEvent) {
  clickNode('NORT-12');
  await fromCard(user, 'Session');
  await fromCard(user, 'Dev env');
}

/** The body's half on `side`, or null when that half is not showing. */
const half = (side: 'left' | 'right') =>
  tabBody().querySelector<HTMLElement>(`[data-side="${side}"]:not([hidden])`);

const tab = (key: string) => tabStrip().querySelector<HTMLElement>(`[data-tab-key="${key}"]`)!;

async function shiftClick(user: UserEvent, key: string) {
  await user.keyboard('{Shift>}');
  await user.click(tab(key));
  await user.keyboard('{/Shift}');
}

describe('a dev env tab', () => {
  it("opens a running service in its worktree's group, framed", async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('NORT-12');
    await fromCard(user, 'Dev env');

    expect(stripKeys()).toEqual([WEB_TAB]);
    expect(worktreeRow('northwind delta')).toHaveAttribute('aria-selected', 'true');
    const frame = within(tabBody()).getByTitle('delta web');
    expect(frame.tagName).toBe('IFRAME');
    expect(frame).toHaveAttribute('src', 'http://localhost:4210');
    expect(within(tabBody()).getByRole('link', { name: /localhost:4210/ })).toHaveAttribute(
      'target',
      '_blank',
    );
  });

  it('keeps its frame mounted while another tab is in view', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('NORT-12');
    await fromCard(user, 'Dev env');
    const frame = within(tabBody()).getByTitle('delta web');

    await fromCard(user, 'Session');
    expect(screen.getByTestId('session-tab')).toBeInTheDocument();
    expect(frame).toBeInTheDocument();
    expect(frame.closest('[hidden]')).not.toBeNull();

    await user.click(tab(WEB_TAB));
    expect(within(tabBody()).getByTitle('delta web')).toBe(frame);
    expect(frame.closest('[hidden]')).toBeNull();
  });

  it('offers to start a stopped service, and draws no frame', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('NORT-12');
    await fromCard(user, 'Dev env');
    await user.click(within(expanded()).getByRole('button', { name: 'Stop env' }));

    await waitFor(() => expect(tabBody()).toHaveTextContent('web is not running.'));
    expect(within(tabBody()).queryByTitle('delta web')).toBeNull();
    expect(within(tabBody()).getByRole('button', { name: 'Start env' })).toBeInTheDocument();
  });

  it('stays a link out of the app on the narrow layout', async () => {
    const user = userEvent.setup();
    await renderApp({ viewport: 'narrow' });
    await user.click(screen.getByRole('link', { name: /Rotate auth tokens/ }));
    const link = within(screen.getByRole('dialog')).getByRole('link', { name: 'Dev env' });
    expect(link).toHaveAttribute('href', 'http://localhost:4210');
    expect(link).toHaveAttribute('target', '_blank');
  });
});

describe('a split tab', () => {
  it('shows beside the active tab after a shift-click, and leaves after another', async () => {
    const user = userEvent.setup();
    await renderApp();
    await openSessionAndWeb(user);
    const frame = within(tabBody()).getByTitle('delta web');

    await shiftClick(user, WEB_TAB);
    expect(within(half('left')!).getByTestId('session-tab')).toBeInTheDocument();
    // The same frame, moved: a move between halves does not reload the app.
    expect(within(half('right')!).getByTitle('delta web')).toBe(frame);
    expect(tab(WEB_TAB)).toHaveAttribute('data-split');
    expect(tab(WEB_TAB)).toHaveAttribute('aria-selected', 'true');
    expect(tab(DELTA_SESSION)).toHaveAttribute('aria-selected', 'true');

    await shiftClick(user, WEB_TAB);
    expect(half('right')).toBeNull();
    expect(within(half('left')!).getByTestId('session-tab')).toBeInTheDocument();
  });

  it('does not split a lone tab', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('NORT-12');
    await fromCard(user, 'Dev env');

    await shiftClick(user, WEB_TAB);
    expect(half('right')).toBeNull();
    expect(tab(WEB_TAB)).not.toHaveAttribute('data-split');
  });

  it('splits an inactive tab beside the active one, and a second replaces the first', async () => {
    const user = userEvent.setup();
    await renderApp();
    await openSessionAndWeb(user);
    await fromCard(user, 'Changes');
    await user.click(tab(WEB_TAB));

    await shiftClick(user, DELTA_SESSION);
    expect(within(half('left')!).getByTitle('delta web')).toBeInTheDocument();
    expect(within(half('right')!).getByTestId('session-tab')).toBeInTheDocument();

    await shiftClick(user, DELTA_CHANGES);
    expect(tab(DELTA_CHANGES)).toHaveAttribute('data-split');
    expect(tab(DELTA_SESSION)).not.toHaveAttribute('data-split');
    expect(within(half('left')!).getByTitle('delta web')).toBeInTheDocument();
    expect(within(half('right')!).queryByTestId('session-tab')).toBeNull();
  });

  it('stays put on a click, and ends when a link opens it', async () => {
    const user = userEvent.setup();
    await renderApp();
    await openSessionAndWeb(user);
    await shiftClick(user, WEB_TAB);

    await user.click(tab(WEB_TAB));
    expect(within(half('left')!).getByTestId('session-tab')).toBeInTheDocument();
    expect(within(half('right')!).getByTitle('delta web')).toBeInTheDocument();

    await fromCard(user, 'Dev env');
    expect(half('right')).toBeNull();
    expect(within(half('left')!).getByTitle('delta web')).toBeInTheDocument();
  });

  it('belongs to its worktree group, and returns with it', async () => {
    const user = userEvent.setup();
    await renderApp();
    await openSession(user, 'NORT-7');
    await openSessionAndWeb(user);
    tab(WEB_TAB).focus();
    await user.keyboard('{Shift>}{Enter}{/Shift}');
    expect(half('right')).not.toBeNull();

    await user.click(worktreeRow('northwind alpha'));
    expect(half('right')).toBeNull();
    await user.click(worktreeRow('northwind delta'));
    expect(within(half('left')!).getByTestId('session-tab')).toBeInTheDocument();
    expect(within(half('right')!).getByTitle('delta web')).toBeInTheDocument();
  });

  it('ends when the split tab closes', async () => {
    const user = userEvent.setup();
    await renderApp();
    await openSessionAndWeb(user);
    await shiftClick(user, WEB_TAB);

    await user.click(within(tab(WEB_TAB)).getByRole('button', { name: /^Close/ }));
    expect(stripKeys()).toEqual([DELTA_SESSION]);
    expect(half('right')).toBeNull();
    expect(within(half('left')!).getByTestId('session-tab')).toBeInTheDocument();
  });
});
