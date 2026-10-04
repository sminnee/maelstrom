import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent, { type UserEvent } from '@testing-library/user-event';
import { expanded, stripKeys, tabBody, tabStrip, worktreeRow } from './test/appHelpers';
import { clickNode, renderApp } from './test/renderApp';

/** NORT-12's agent runs in delta, where `web` runs and `ladle` is stopped. */
const WEB_TAB = 'devenv:northwind-delta:web';

/** Press a link on NORT-12's card, which must be open. */
const fromCard = (user: UserEvent, name: string) =>
  user.click(within(expanded()).getByRole('link', { name }));

const tab = (key: string) => tabStrip().querySelector<HTMLElement>(`[data-tab-key="${key}"]`)!;

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
    await user.click(screen.getByRole('button', { name: /Rotate auth tokens/ }));
    const link = within(screen.getByRole('dialog')).getByRole('link', { name: 'Dev env' });
    expect(link).toHaveAttribute('href', 'http://localhost:4210');
    expect(link).toHaveAttribute('target', '_blank');
  });
});
