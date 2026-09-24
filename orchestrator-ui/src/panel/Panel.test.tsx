import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { renderApp, VIEWPORTS } from '../test/renderApp';

const panelWidth = () => Number.parseFloat(screen.getByTestId('panel').style.width);

describe('the panel opens wide enough to read in', () => {
  it('takes half the window, so the canvas keeps the other half', async () => {
    await renderApp();
    // The wide viewport is 1440.
    expect(panelWidth()).toBe(VIEWPORTS.wide / 2);
  });

  it('never opens past the window it has to share', async () => {
    await renderApp();
    // The grip lives on the panel's left edge, so a panel as wide as the
    // window would put it off-screen with no way to drag it back.
    expect(panelWidth()).toBeLessThan(VIEWPORTS.wide);
  });
});

describe('the panel beside each view', () => {
  it('stays beside the task list, but not beside the worktree table', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.click(screen.getByRole('button', { name: 'Tasks' }));
    expect(screen.getByTestId('panel')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Worktrees' }));
    expect(screen.queryByTestId('panel')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Panel' })).toBeNull();
  });

  it('hides behind the Panel toggle, and comes back when it is pressed again', async () => {
    const user = userEvent.setup();
    await renderApp();
    const toggle = screen.getByRole('button', { name: 'Panel' });
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
    await user.click(screen.getByRole('button', { name: 'Tasks' }));
    await user.click(screen.getByRole('button', { name: 'Panel' }));
    // NORT-7 has an agent, so its state cell links to the session.
    const row = screen.getByTestId('task-list').querySelector('[data-task-id="NORT-7"]');
    await user.click(within(row as HTMLElement).getByRole('link'));
    expect(screen.getByTestId('panel')).toBeVisible();
    expect(screen.getByRole('tab', { selected: true })).toHaveAccessibleName(/NORT-7/);
  });
});
