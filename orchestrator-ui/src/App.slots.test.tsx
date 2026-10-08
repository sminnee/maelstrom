import { describe, expect, it } from 'vitest';
import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { tabStrip, paneItem, isShowing } from './test/appHelpers';
import { clickNode, renderApp, resizeTo, VIEWPORTS } from './test/renderApp';

const item = paneItem;
const group = (name: string) => screen.queryByRole('group', { name });
/** The names of a top bar group's items, in the order they are drawn. */
const items = (name: string) =>
  [...screen.getByRole('group', { name }).querySelectorAll('a, button')].map((b) => b.textContent);
const expanded = () => screen.getByRole('dialog');
const rightWidth = () => Number.parseFloat(screen.getByTestId('panel').style.width);
/** Which slot an element is drawn in. */
const slotOf = (testId: string) =>
  screen.getByTestId(testId).closest('[data-slot]')?.getAttribute('data-slot');

describe('the medium layout: one slot', () => {
  it('draws one menu of four, with the desk in front', async () => {
    await renderApp({ viewport: 'medium' });
    expect(items('Views')).toEqual(['Desk', 'Tasks', 'Worktrees', 'Tabs']);
    expect(isShowing('Desk')).toBe(true);
    expect(isShowing('Tabs')).toBe(false);
    expect(screen.getByTestId('canvas')).toBeInTheDocument();
    expect(screen.getByTestId('panel')).not.toBeVisible();
  });

  it('puts the panel in place of the main view, and Desk brings the view back', async () => {
    const user = userEvent.setup();
    await renderApp({ viewport: 'medium' });
    await user.click(item('Tabs'));
    expect(isShowing('Tabs')).toBe(true);
    expect(isShowing('Desk')).toBe(false);
    expect(screen.getByTestId('panel')).toBeVisible();
    expect(screen.queryByTestId('canvas')).not.toBeInTheDocument();
    // A second click does not close the one slot.
    await user.click(item('Tabs'));
    expect(screen.getByTestId('panel')).toBeVisible();
    await user.click(item('Desk'));
    expect(screen.getByTestId('canvas')).toBeInTheDocument();
    expect(screen.getByTestId('panel')).not.toBeVisible();
  });

  it('has no side to move an item to, so a shift-click is the browser’s own', async () => {
    const user = userEvent.setup();
    await renderApp({ viewport: 'medium' });
    await user.keyboard('{Shift>}');
    await user.click(item('Tasks'));
    await user.keyboard('{/Shift}');
    // A shift-click on a link opens a new window: this one keeps its view.
    expect(screen.queryByTestId('task-list')).toBeNull();
    resizeTo('wide');
    expect(items('Left slot')).toEqual(['Desk', 'Tasks', 'Worktrees']);
    expect(items('Right slot')).toEqual(['Tabs']);
  });

  it('returns to the canvas for the attention chip pressed over the panel', async () => {
    const user = userEvent.setup();
    await renderApp({ viewport: 'medium' });
    await user.click(item('Tabs'));
    await user.click(screen.getByTestId('attention-chip'));
    expect(isShowing('Desk')).toBe(true);
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('sends on Enter, as the wide layout does: a laptop has a hardware keyboard', async () => {
    const user = userEvent.setup();
    await renderApp({ viewport: 'medium' });
    clickNode('NORT-9');
    await user.click(within(expanded()).getByRole('link', { name: 'Session' }));
    const input = screen.getByRole('textbox', { name: 'Message to agent' });
    await user.type(input, 'Prefer the ICU collation.{Enter}');
    expect(await screen.findByText('Prefer the ICU collation.')).toBeInTheDocument();
    expect(input).toHaveValue('');
  });

  it('brings the panel to the front for a panel link', async () => {
    const user = userEvent.setup();
    await renderApp({ viewport: 'medium' });
    await user.click(item('Tasks'));
    const row = screen.getByTestId('task-list').querySelector('[data-task-id="NORT-7"]');
    await user.click(within(row as HTMLElement).getByRole('link', { name: /needs you/i }));
    expect(screen.getByTestId('panel')).toBeVisible();
    expect(screen.queryByTestId('task-list')).not.toBeInTheDocument();
    expect(isShowing('Tabs')).toBe(true);
    expect(within(tabStrip()).getByRole('tab', { selected: true })).toHaveAccessibleName(/NORT-7/);
  });
});

describe('the wide layout: two slots', () => {
  it('splits the menu by anchor: the main views left, Tabs right', async () => {
    await renderApp();
    expect(items('Left slot')).toEqual(['Desk', 'Tasks', 'Worktrees']);
    expect(items('Right slot')).toEqual(['Tabs']);
    expect(slotOf('canvas')).toBe('left');
    expect(slotOf('panel')).toBe('right');
  });

  it('opens the right slot at half the window, behind a grip', async () => {
    await renderApp();
    expect(rightWidth()).toBe(VIEWPORTS.wide / 2);
    expect(screen.getByTestId('grip')).toBeInTheDocument();
  });

  it('leaves the panel alone when the left slot closes', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.click(item('Desk'));
    expect(isShowing('Desk')).toBe(false);
    expect(screen.queryByTestId('canvas')).not.toBeInTheDocument();
    expect(screen.getByTestId('panel')).toBeVisible();
    // Alone, the right slot takes the full width rather than the dragged one,
    // and there is no second slot to drag a grip against.
    expect(screen.getByTestId('panel').style.width).toBe('');
    expect(screen.queryByTestId('grip')).toBeNull();
  });

  it('reopens the left slot on its last view when the last slot closes', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.click(item('Tasks'));
    await user.click(item('Tasks'));
    await user.click(item('Tabs'));
    expect(screen.getByTestId('task-list')).toBeInTheDocument();
    expect(isShowing('Tasks')).toBe(true);
    expect(screen.getByTestId('panel')).not.toBeVisible();
  });

  it('moves an item to the other group, and its pane to the other slot, on shift-click', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.keyboard('{Shift>}');
    await user.click(item('Tasks'));
    await user.keyboard('{/Shift}');
    expect(items('Left slot')).toEqual(['Desk', 'Worktrees']);
    expect(items('Right slot')).toEqual(['Tasks', 'Tabs']);
    // Desk and Tasks now show together.
    expect(slotOf('canvas')).toBe('left');
    expect(slotOf('task-list')).toBe('right');
    expect(screen.getByTestId('panel')).not.toBeVisible();
  });

  it('draws no group for a side with no item anchored to it', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.keyboard('{Shift>}');
    await user.click(item('Tabs'));
    await user.keyboard('{/Shift}');
    expect(group('Right slot')).toBeNull();
    expect(items('Left slot')).toEqual(['Desk', 'Tasks', 'Worktrees', 'Tabs']);
  });

  it('keeps the panel mounted across an anchor move', async () => {
    const user = userEvent.setup();
    await renderApp();
    const before = screen.getByTestId('panel');
    await user.keyboard('{Shift>}');
    await user.click(item('Tabs'));
    await user.keyboard('{/Shift}');
    expect(screen.getByTestId('panel')).toBe(before);
    expect(slotOf('panel')).toBe('left');
    expect(screen.getByTestId('panel')).toBeVisible();
  });

  it('draws each showing view its own filters, under one project filter', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.keyboard('{Shift>}');
    await user.click(item('Tasks'));
    await user.keyboard('{/Shift}');
    expect(screen.getAllByLabelText('Project')).toHaveLength(1);
    expect(screen.getByLabelText('Agent status')).toBeInTheDocument();
    expect(screen.getByLabelText('Search')).toBeInTheDocument();
    // With only the panel on screen, nothing but the project is left to filter.
    await user.click(item('Desk'));
    await user.click(item('Tabs'));
    expect(screen.getByLabelText('Project')).toBeInTheDocument();
    expect(screen.queryByLabelText('Branch')).toBeNull();
    expect(screen.queryByLabelText('Agent status')).toBeNull();
    expect(screen.queryByLabelText('Search')).toBeNull();
  });

  it('draws Branch for the desk beside the worktree table, but not for the table alone', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.keyboard('{Shift>}');
    await user.click(item('Worktrees'));
    await user.keyboard('{/Shift}');
    expect(screen.getByLabelText('show closed')).toBeInTheDocument();
    expect(screen.getByLabelText('Branch')).toBeInTheDocument();
    await user.click(item('Desk'));
    expect(screen.getByLabelText('show closed')).toBeInTheDocument();
    expect(screen.queryByLabelText('Branch')).toBeNull();
  });
});

describe('a resize across 1600px', () => {
  it('draws the pane last selected, and keeps the panel mounted', async () => {
    const user = userEvent.setup();
    await renderApp();
    const panel = screen.getByTestId('panel');
    // The desk and the panel both show; the panel was selected last.
    await user.click(item('Tabs'));
    await user.click(item('Tabs'));
    resizeTo('medium');
    expect(isShowing('Tabs')).toBe(true);
    expect(screen.getByTestId('panel')).toBe(panel);
    expect(panel).toBeVisible();
    expect(screen.queryByTestId('canvas')).not.toBeInTheDocument();
    resizeTo('wide');
    expect(screen.getByTestId('panel')).toBe(panel);
    expect(screen.getByTestId('canvas')).toBeInTheDocument();
    expect(panel).toBeVisible();
  });

  it('draws the open slot when the other one closed in the wide layout', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.click(item('Desk'));
    resizeTo('medium');
    expect(screen.getByTestId('panel')).toBeVisible();
    expect(isShowing('Tabs')).toBe(true);
  });
});

describe('the view in the URL', () => {
  it('opens on the view the path names', async () => {
    await renderApp({ url: '/tasks' });
    expect(screen.getByTestId('task-list')).toBeInTheDocument();
    expect(isShowing('Tasks')).toBe(true);
  });

  it('moves to the view a menu item links to, and Back returns', async () => {
    const user = userEvent.setup();
    const { router } = await renderApp({ viewport: 'medium' });
    expect(item('Worktrees')).toHaveAttribute('href', '/worktrees');
    await user.click(item('Worktrees'));
    expect(router.state.location.pathname).toBe('/worktrees');
    expect(screen.getByTestId('worktree-table')).toBeInTheDocument();
    await act(() => router.navigate(-1));
    expect(router.state.location.pathname).toBe('/desk');
    expect(screen.getByTestId('canvas')).toBeInTheDocument();
  });

  it('moves a path with no screen to the desk', async () => {
    const { router } = await renderApp({ url: '/?panel=session/d9a4c7f1' });
    expect(router.state.location.pathname).toBe('/desk');
    expect(router.state.location.search).toBe('?panel=session/d9a4c7f1');
    expect(router.state.historyAction).toBe('REPLACE');
    expect(screen.getByTestId('canvas')).toBeInTheDocument();
  });

  it('brings the view back to the front on the medium layout when Back leaves the panel', async () => {
    const user = userEvent.setup();
    const { router } = await renderApp({ viewport: 'medium' });
    clickNode('NORT-9');
    await user.click(within(screen.getByRole('dialog')).getByRole('link', { name: 'Session' }));
    expect(isShowing('Tabs')).toBe(true);
    await act(() => router.navigate(-1));
    expect(isShowing('Desk')).toBe(true);
    expect(screen.getByTestId('canvas')).toBeInTheDocument();
  });

  it('draws the view the path names on the narrow layout', async () => {
    await renderApp({ viewport: 'narrow', url: '/tasks' });
    expect(screen.getByTestId('task-list')).toBeInTheDocument();
  });
});
