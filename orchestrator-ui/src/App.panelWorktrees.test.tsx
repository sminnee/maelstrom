import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expanded, openSession, stripKeys, tabStrip, worktreeRow } from './test/appHelpers';
import { renderApp } from './test/renderApp';

const sidebar = () => screen.getByRole('tablist', { name: 'Worktrees' });
const rows = () =>
  within(sidebar())
    .queryAllByRole('tab')
    .map((r) => r.getAttribute('aria-label'));

describe('the panel sidebar groups tabs by worktree', () => {
  it('lists one row per worktree under its project, and shows only the selected one’s tabs', async () => {
    const user = userEvent.setup();
    await renderApp();
    // NORT-7 runs in northwind-alpha, NORT-9 in northwind-bravo.
    await openSession(user, 'NORT-7');
    await user.click(within(expanded()).getByRole('link', { name: /Plan v1/ }));
    await openSession(user, 'NORT-9');

    expect(within(sidebar()).getByText('northwind')).toBeInTheDocument();
    expect(rows()).toEqual(['northwind alpha', 'northwind bravo']);
    expect(within(sidebar()).getByRole('tab', { selected: true })).toHaveAttribute(
      'aria-label',
      'northwind bravo',
    );
    expect(stripKeys()).toEqual(['session:d9a4c7f1']);
    // The sidebar names the project, so the tab does not.
    expect(within(tabStrip()).getByTestId('tab-chip')).toHaveTextContent(/^NORT-9$/);

    // Back in alpha, the tab last in view there comes back, not the first.
    await user.click(worktreeRow('northwind alpha'));
    expect(stripKeys()).toEqual(['session:a1f3c9e2', 'document:doc-nort7-plan']);
    expect(within(tabStrip()).getByRole('tab', { selected: true })).toHaveAttribute(
      'data-tab-key',
      'document:doc-nort7-plan',
    );
    await user.click(within(tabStrip()).getByRole('tab', { name: /^NORT-7$/ }));
    await user.click(worktreeRow('northwind bravo'));
    await user.click(worktreeRow('northwind alpha'));
    expect(within(tabStrip()).getByRole('tab', { selected: true })).toHaveAttribute(
      'data-tab-key',
      'session:a1f3c9e2',
    );
  });

  it('moves between rows with Up and Down, wrapping at either end', async () => {
    const user = userEvent.setup();
    await renderApp();
    await openSession(user, 'NORT-7');
    await openSession(user, 'NORT-9');

    worktreeRow('northwind bravo').focus();
    await user.keyboard('{ArrowDown}');
    expect(worktreeRow('northwind alpha')).toHaveAttribute('aria-selected', 'true');
    expect(worktreeRow('northwind alpha')).toHaveFocus();
    expect(stripKeys()).toEqual(['session:a1f3c9e2']);
    await user.keyboard('{ArrowUp}');
    expect(worktreeRow('northwind bravo')).toHaveAttribute('aria-selected', 'true');
    expect(worktreeRow('northwind bravo')).toHaveFocus();
    expect(stripKeys()).toEqual(['session:d9a4c7f1']);
  });

  it('closes every tab of a worktree from its row, and the row goes', async () => {
    const user = userEvent.setup();
    await renderApp();
    await openSession(user, 'NORT-7');
    await user.click(within(expanded()).getByRole('link', { name: /Plan v1/ }));
    await openSession(user, 'NORT-9');

    await user.click(within(sidebar()).getByRole('button', { name: 'Close northwind alpha' }));
    expect(rows()).toEqual(['northwind bravo']);
    expect(stripKeys()).toEqual(['session:d9a4c7f1']);
  });

  it('shows the commands of the worktree in view in the worktree bar', async () => {
    const user = userEvent.setup();
    await renderApp();
    await openSession(user, 'NORT-7');
    await openSession(user, 'NORT-9');
    const bar = () => screen.getByTestId('worktree-bar');
    expect(bar()).toHaveTextContent('feat/db-migrate');
    expect(within(bar()).getByRole('button', { name: 'Sync' })).toBeInTheDocument();
    expect(within(bar()).getByRole('button', { name: 'Start env' })).toBeInTheDocument();

    await user.click(worktreeRow('northwind alpha'));
    expect(bar()).toHaveTextContent('feat/orders');
  });
});
