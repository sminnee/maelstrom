import { describe, expect, it } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { act } from 'react';
import userEvent from '@testing-library/user-event';
import { renderApp } from './test/renderApp';

describe('loading', () => {
  it('shows Loading, not "No task matches", before the tasks arrive', async () => {
    const user = userEvent.setup();
    await renderApp({ ready: false });
    expect(screen.getByTestId('canvas-loading')).toHaveTextContent('Loading the world…');
    await user.click(screen.getByRole('button', { name: 'Task list' }));
    expect(screen.getByTestId('task-list')).toHaveTextContent('Loading…');
    expect(screen.getByTestId('task-list')).not.toHaveTextContent('No task matches');
  });

  it('shows the error and retries when a list cannot be read', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp({ ready: false });
    server.refuse(/GET \/api\/tasks$/, { status: 502, code: 'invalid', message: 'bad gateway' });
    await act(async () => {
      server.release();
    });
    expect(await screen.findByTestId('canvas-error')).toHaveTextContent('bad gateway');
    server.allow();
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByTestId('canvas')).toBeInTheDocument();
  });
});

describe('the change stream', () => {
  it('shows the banner while the stream reconnects, and keeps the nodes', async () => {
    const { server } = await renderApp();
    await act(async () => {});
    expect(screen.queryByRole('status')).toBeNull();
    await act(async () => {
      server.dropStream();
    });
    expect(screen.getByRole('status')).toHaveTextContent(
      'Reconnecting… showing the last known state',
    );
    expect(screen.getAllByTestId('task-node').length).toBeGreaterThan(0);
    await act(async () => {
      server.openStreams();
    });
    expect(screen.queryByRole('status')).toBeNull();
  });
});

describe('the agent host', () => {
  it('says when the host stopped answering, keeps the agents, and clears when it is back', async () => {
    const { server } = await renderApp();
    await act(async () => {});
    expect(screen.queryByRole('status')).toBeNull();
    await act(async () => {
      server.change({ kind: 'host', ids: ['agent-host'] }, (world) => {
        world.host = {
          id: 'agent-host',
          reachable: false,
          since: '2026-06-11T09:05:00Z',
          socket: '/x/agent-daemon.sock',
          usage: null,
        };
      });
    });
    const banner = await screen.findByRole('status');
    expect(banner).toHaveTextContent('Agent host unreachable since');
    expect(banner).toHaveTextContent('showing the last known agents');
    expect(banner).toHaveTextContent('mael self-env start');
    // The agents are the last known ones, still drawn.
    expect(screen.getAllByTestId('task-node').length).toBeGreaterThan(0);
    await act(async () => {
      server.change({ kind: 'host', ids: ['agent-host'] }, (world) => {
        world.host = { ...world.host!, reachable: true, since: '2026-06-11T09:06:00Z' };
      });
    });
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
  });
});
