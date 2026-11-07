import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expanded } from './test/appHelpers';
import { clickNode, renderApp } from './test/renderApp';

/**
 * The `remote-host` scenario: NORT-7.1 can launch, `cobalt` is online and
 * `zinc` is not. NORT-9's agent runs on `zinc`.
 */
const launches = (server: { requests: { method: string; path: string; body: unknown }[] }) =>
  server.requests.filter((r) => r.method === 'POST' && r.path.endsWith('/launch'));

describe('the Launch button of a task card', () => {
  it('launches here by default, and offers each online remote host in its menu', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp({ scenario: 'remote-host' });
    clickNode('NORT-7.1');
    const card = screen.getByRole('dialog');

    expect(within(card).getByRole('button', { name: 'Launch here' })).toBeInTheDocument();
    await user.click(within(card).getByRole('button', { name: 'Launch on another host' }));
    // An offline host is not offered.
    expect(screen.getAllByRole('menuitem').map((item) => item.getAttribute('aria-label'))).toEqual([
      'Launch here',
      'Launch on cobalt',
    ]);
    await user.click(screen.getByRole('menuitem', { name: 'Launch on cobalt' }));

    await waitFor(() => expect(launches(server)).toHaveLength(1));
    expect(launches(server)[0]!.body).toEqual({ host: 'cobalt' });
  });

  it('is a plain Launch with no remote host online', async () => {
    const user = userEvent.setup();
    // `launch-fails` is the scenario with NORT-7.1 launchable and, from
    // seedWorld, `remoteHosts: []`. Its launch fails after the request is sent.
    const { server } = await renderApp({ scenario: 'launch-fails' });
    clickNode('NORT-7.1');
    const card = screen.getByRole('dialog');

    expect(
      within(card).queryByRole('button', { name: 'Launch on another host' }),
    ).not.toBeInTheDocument();
    await user.click(within(card).getByRole('button', { name: 'Launch' }));

    await waitFor(() => expect(launches(server)).toHaveLength(1));
    expect(launches(server)[0]!.body).toEqual({});
  });
});

describe('the session tab of an agent on a remote host', () => {
  it('names the host, and marks it offline', async () => {
    const user = userEvent.setup();
    await renderApp({ scenario: 'remote-host' });
    clickNode('NORT-9');
    await user.click(within(expanded()).getByRole('link', { name: 'Session' }));
    expect(await screen.findByText(/zinc offline/)).toBeInTheDocument();
  });
});
