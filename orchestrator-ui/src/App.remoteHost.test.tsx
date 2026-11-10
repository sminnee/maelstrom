import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expanded, worktreeBox } from './test/appHelpers';
import { clickNode, renderApp } from './test/renderApp';

/**
 * The `remote-host` scenario: NORT-7.1 can launch, `cobalt` is online and
 * `zinc` is not. NORT-9's agent runs on `zinc`.
 */
const launches = (server: { requests: { method: string; path: string; body: unknown }[] }) =>
  server.requests.filter((r) => r.method === 'POST' && r.path.endsWith('/launch'));

describe('the Launch button of a task card', () => {
  it('launches on this server by default, and offers each online remote host in its menu', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp({ scenario: 'remote-host' });
    clickNode('NORT-7.1');
    const card = screen.getByRole('dialog');

    expect(within(card).getByRole('button', { name: 'Launch' })).toBeInTheDocument();
    await user.click(within(card).getByRole('button', { name: 'Launch on another host' }));
    // An offline host is not offered.
    expect(screen.getAllByRole('menuitem').map((item) => item.getAttribute('aria-label'))).toEqual([
      'Launch on monolith',
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

describe('an operation a remote host runs', () => {
  it('marks its worktree busy here, and shows in the console strip', async () => {
    await renderApp({
      scenario: 'remote-host',
      url: '/projects?detail=northwind&dtab=worktrees',
    });
    const row = await waitFor(() => {
      const found = document.querySelector<HTMLElement>(
        '[data-testid="worktree-table"] [data-worktree-id="cobalt:northwind-echo"]',
      );
      expect(found).not.toBeNull();
      return found!;
    });
    expect(row).toHaveAttribute('data-operation', 'running');
    // Another host's worktree is named with its owner, so it is not this server's echo.
    expect(within(row).getByText('cobalt:echo')).toBeInTheDocument();
    expect(within(row).getByText(/^Closing echo/)).toBeInTheDocument();
    const strip = screen.getByRole('region', { name: 'Operations now' });
    expect(strip.querySelector('[data-operation-id="cobalt:op3"]')).toHaveTextContent(
      'Closing echo',
    );
  });
});

describe('a worktree another host owns', () => {
  it('names its owner on the board', async () => {
    await renderApp({ scenario: 'remote-host' });
    const box = await waitFor(() => worktreeBox('cobalt:northwind-echo')!);
    expect(within(box).getByText('cobalt:echo')).toBeInTheDocument();
  });
});

describe("the remote host's board", () => {
  it('marks the worktree a delegated agent uses with its parent, and no other', async () => {
    await renderApp({ scenario: 'delegated-agent' });
    const delta = await waitFor(() => worktreeBox('cobalt:northwind-delta')!);
    // cobalt owns delta, so its own name stays bare.
    expect(within(delta).getByText('delta')).toBeInTheDocument();
    expect(within(delta).getByText(/in use by monolith/)).toBeInTheDocument();
    expect(worktreeBox('northwind-alpha')).not.toHaveTextContent(/in use by/);
  });
});
