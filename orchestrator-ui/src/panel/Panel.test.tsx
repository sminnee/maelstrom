import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { tabStrip } from '../test/appHelpers';
import { renderApp } from '../test/renderApp';

describe('the panel beside each view', () => {
  it('stays beside the task list and the worktree table', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.click(screen.getByRole('button', { name: 'Tasks' }));
    expect(screen.getByTestId('panel')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Worktrees' }));
    expect(screen.getByTestId('panel')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Tabs' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('hides behind the Tabs item, and comes back when it is pressed again', async () => {
    const user = userEvent.setup();
    await renderApp();
    const toggle = screen.getByRole('button', { name: 'Tabs' });
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
    await user.click(screen.getByRole('button', { name: 'Tabs' }));
    // NORT-7 has an agent, so its state cell links to the session.
    const row = screen.getByTestId('task-list').querySelector('[data-task-id="NORT-7"]');
    await user.click(within(row as HTMLElement).getByRole('link'));
    expect(screen.getByTestId('panel')).toBeVisible();
    expect(within(tabStrip()).getByRole('tab', { selected: true })).toHaveAccessibleName(/NORT-7/);
  });
});
