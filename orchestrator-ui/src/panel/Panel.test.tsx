import { describe, expect, it } from 'vitest';
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import {
  expanded,
  isShowing,
  openSession,
  paneItem,
  stripKeys,
  tabStrip,
  worktreeRow,
} from '../test/appHelpers';
import { clickNode, renderApp } from '../test/renderApp';

describe('the panel beside each view', () => {
  it('stays beside the task list and the worktree table', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.click(paneItem('Tasks'));
    expect(screen.getByTestId('panel')).toBeVisible();
    await user.click(paneItem('Worktrees'));
    expect(screen.getByTestId('panel')).toBeVisible();
    expect(isShowing('Tabs')).toBe(true);
  });

  it('hides behind the Tabs item, and comes back when it is pressed again', async () => {
    const user = userEvent.setup();
    await renderApp();
    const toggle = paneItem('Tabs');
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByTestId('panel')).not.toBeVisible();
    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('panel')).toBeVisible();
  });

  it('opens again for a panel link pressed while it is collapsed', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.click(paneItem('Tasks'));
    await user.click(paneItem('Tabs'));
    // NORT-7 has an agent, so its state cell links to the session.
    const row = screen.getByTestId('task-list').querySelector('[data-task-id="NORT-7"]');
    await user.click(within(row as HTMLElement).getByRole('link'));
    expect(screen.getByTestId('panel')).toBeVisible();
    expect(within(tabStrip()).getByRole('tab', { selected: true })).toHaveAccessibleName(/NORT-7/);
  });
});

describe('the panel tab in the URL', () => {
  const selected = () => within(tabStrip()).getByRole('tab', { selected: true });

  it('opens on the tab the URL names', async () => {
    await renderApp({ url: '/desk?panel=session/d9a4c7f1' });
    expect(selected()).toHaveAccessibleName(/NORT-9/);
    expect(screen.getByTestId('session-tab')).toBeInTheDocument();
  });

  it('links a tab at its URL, and Back returns to the tab before', async () => {
    const user = userEvent.setup();
    const { router } = await renderApp();
    clickNode('NORT-9');
    const link = within(expanded()).getByRole('link', { name: 'Session' });
    expect(link).toHaveAttribute('href', '/desk/task/NORT-9?panel=session/d9a4c7f1');
    await user.click(link);
    expect(router.state.location.search).toBe('?panel=session/d9a4c7f1');
    await openSession(user, 'NORT-7');
    expect(selected()).toHaveAccessibleName(/NORT-7/);
    await act(() => router.navigate(-1));
    expect(selected()).toHaveAccessibleName(/NORT-9/);
  });

  it('moves to another tab for a click on it in the strip', async () => {
    const user = userEvent.setup();
    const { router } = await renderApp({ url: '/desk?panel=session/d9a4c7f1' });
    await openSession(user, 'NORT-7');
    await user.click(within(tabStrip()).getByRole('tab', { name: /NORT-7/ }));
    expect(router.state.location.search).toBe('?panel=session/a1f3c9e2');
  });

  it('replaces the location with the tab that takes over from a closed one', async () => {
    const user = userEvent.setup();
    const { router } = await renderApp();
    await openSession(user, 'NORT-9');
    await openSession(user, 'NORT-7');
    await user.click(screen.getByRole('button', { name: 'Close NORT-7' }));
    await waitFor(() => expect(router.state.location.search).toBe('?panel=session/d9a4c7f1'));
    expect(router.state.historyAction).toBe('REPLACE');
  });

  it('draws the tab as a screen over the card on the narrow layout, and Back pops each', async () => {
    const user = userEvent.setup();
    await renderApp({ viewport: 'narrow', url: '/desk/task/NORT-9?panel=session/d9a4c7f1' });
    expect(screen.getByTestId('session-tab')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Back' }));
    expect(await screen.findByRole('dialog')).toHaveTextContent('Migrate to Postgres 16');
    await user.click(screen.getByRole('button', { name: 'Back' }));
    expect(await screen.findByTestId('deck-list')).toBeInTheDocument();
  });
});

describe('the open tabs across a refresh', () => {
  it('keeps every open tab, and shows the one the URL names', async () => {
    const user = userEvent.setup();
    const { unmount } = await renderApp();
    await openSession(user, 'NORT-9');
    await openSession(user, 'NORT-7');
    unmount();
    await renderApp({ url: '/desk?panel=session/d9a4c7f1' });
    // One tab in each worktree: both rows are back, and the strip shows the named one.
    expect(worktreeRow('northwind alpha')).toBeInTheDocument();
    expect(stripKeys()).toEqual(['session:d9a4c7f1']);
    expect(within(tabStrip()).getByRole('tab', { selected: true })).toHaveAccessibleName(/NORT-9/);
  });
});
