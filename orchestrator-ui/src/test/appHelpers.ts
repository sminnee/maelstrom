import { screen, within } from '@testing-library/react';
import type { UserEvent } from '@testing-library/user-event';
import type { FakeServer } from '../fake/fakeServer';
import { clickNode } from './renderApp';

/**
 * Reading and moving a rendered app, for the `App*.test.tsx` files.
 *
 * These sit beside `renderApp` rather than inside it: `renderApp` mounts the
 * app, and these read the DOM it produced or push the world past it.
 */

/** The one expanded node, as the card it grew into. */
export const expanded = () => screen.getByRole('dialog');

/** The panel's tab strip: the tabs of the worktree in view. */
export const tabStrip = () => screen.getByRole('tablist', { name: 'Open tabs' });

/** The panel's sidebar row for a worktree, named `<project> <nato>`. */
export const worktreeRow = (name: string) =>
  within(screen.getByRole('tablist', { name: 'Worktrees' })).getByRole('tab', { name });

/**
 * The active tab's body. Not found by role alone: the worktree group around it
 * is the sidebar's tabpanel, so the page holds two.
 */
export const tabBody = () => screen.getByTestId('panel-body');

/** The keys of the tabs in the strip, in strip order. */
export const stripKeys = () =>
  within(tabStrip())
    .getAllByRole('tab')
    .map((t) => t.getAttribute('data-tab-key'));

/** Open a node's session from its expanded card. */
export async function openSession(user: UserEvent, taskId: string) {
  clickNode(taskId);
  await user.click(within(expanded()).getByRole('link', { name: 'Session' }));
}

/** The attention count the chip shows. */
export const chipCount = () =>
  Number(screen.getByTestId('attention-count').textContent?.replace(/\D/g, ''));

/** The unanswered count the chip shows. */
export const unansweredCount = () =>
  Number(screen.queryByTestId('unanswered-count')?.textContent ?? 0);

/** The `data-state` a task's node draws with, or undefined when it draws none. */
export const nodeState = (taskId: string) =>
  document.querySelector(`[data-task-id="${taskId}"]`)?.getAttribute('data-state');

/** The commands sent since request `from`: every call but a read, path decoded. */
export function commandsSince(server: FakeServer, from: number): string[] {
  return server.requests
    .slice(from)
    .filter((r) => r.method !== 'GET')
    .map((r) => `${r.method} ${decodeURIComponent(r.path)}`);
}

/** Stop an agent, as a terminate does: the row stays in the world, exited. */
export function exitAgent(server: FakeServer, agentId: string) {
  server.change({ kind: 'agent', ids: [agentId] }, (w) => {
    w.agents[agentId] = {
      ...w.agents[agentId]!,
      state: 'exited',
      exitCode: 0,
      pendingRequestIds: [],
    };
  });
}

/**
 * Every control of the worktree area in `dialog`, in order: its label, and
 * whether it is held. One reading for both cards, so the two are compared whole.
 */
export function worktreeControls(dialog: HTMLElement): [string | null, boolean][] {
  const area = within(dialog).getByRole('region', { name: 'Worktree' });
  return [...within(area).getAllByRole('link'), ...within(area).getAllByRole('button')].map(
    (el) => [
      el.getAttribute('aria-label') ?? el.textContent,
      (el as HTMLButtonElement).disabled === true,
    ],
  );
}
