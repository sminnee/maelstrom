import { fireEvent, screen, within } from '@testing-library/react';
import { vi } from 'vitest';
import type { UserEvent } from '@testing-library/user-event';
import type { FakeServer } from '../fake/fakeServer';
import { clickNode } from './renderApp';

/**
 * Reading and moving a rendered app, for the `App*.test.tsx` files.
 *
 * These sit beside `renderApp` rather than inside it: `renderApp` mounts the
 * app, and these read the DOM it produced or push the world past it.
 */

/** A top bar menu item: a link to a main view, or the `Tabs` button. */
export const paneItem = (name: string) =>
  screen.getByRole(name === 'Tabs' ? 'button' : 'link', { name });

/** Whether a top bar item's pane is on screen. */
export const isShowing = (name: string) => {
  const item = paneItem(name);
  return item.getAttribute(item.tagName === 'A' ? 'aria-current' : 'aria-pressed') === 'true';
};

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

/** The narrow layout's top bar: the screen strip on a pushed screen. */
export const screenStrip = () => within(screen.getByTestId('top-bar'));

/** Open the side sheet of a pushed narrow screen from More, and read inside it. */
export async function openSheet(user: UserEvent) {
  await user.click(screenStrip().getByRole('button', { name: 'More' }));
  return within(screen.getByRole('dialog', { name: 'More' }));
}

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

/**
 * A pointer drag on `el`: a press at `from`, moves in 10px steps to `from + by`,
 * then a release unless `release` is false.
 *
 * jsdom stamps an event with `Date.now()`, so the clock is stepped
 * `msPerStep` per move. That makes the drag's speed a choice of the test: the
 * default 50ms a step is slow, so no flick closes anything by accident.
 */
export function touchDrag(
  el: Element,
  {
    from,
    by,
    msPerStep = 50,
    release = true,
    pointerType = 'touch',
  }: {
    from: { x: number; y: number };
    by: { x?: number; y?: number };
    msPerStep?: number;
    release?: boolean;
    pointerType?: 'touch' | 'mouse';
  },
) {
  const dx = by.x ?? 0;
  const dy = by.y ?? 0;
  const at = { pointerId: 1, pointerType, isPrimary: true };
  let now = Date.now();
  const clock = vi.spyOn(Date, 'now').mockImplementation(() => now);
  try {
    fireEvent.pointerDown(el, { ...at, clientX: from.x, clientY: from.y });
    const steps = Math.max(Math.abs(dx), Math.abs(dy)) / 10;
    for (let i = 1; i <= steps; i++) {
      now += msPerStep;
      fireEvent.pointerMove(el, {
        ...at,
        clientX: from.x + (dx * i) / steps,
        clientY: from.y + (dy * i) / steps,
      });
    }
    if (release) fireEvent.pointerUp(el, { ...at, clientX: from.x + dx, clientY: from.y + dy });
  } finally {
    clock.mockRestore();
  }
}
