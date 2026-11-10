import { paneItem } from './test/appHelpers';
import { describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { act } from 'react';
import userEvent from '@testing-library/user-event';
import { renderApp } from './test/renderApp';
import { SILENT_DROP_MS, STALE_DROP_MS } from './shell/ConnectionBanner';

describe('loading', () => {
  it('shows Loading, not "No task matches", before the tasks arrive', async () => {
    const user = userEvent.setup();
    await renderApp({ ready: false });
    expect(screen.getByTestId('canvas-loading')).toHaveTextContent('Loading the world…');
    await user.click(paneItem('Tasks'));
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
  it('shows "Connecting…" at once, before any data', async () => {
    await renderApp({ ready: false });
    expect(screen.getByRole('status')).toHaveTextContent(/^Connecting…$/);
  });

  it('waits out a short drop, then shows a grey and then an orange banner, and keeps the nodes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { server } = await renderApp();
      await act(async () => {});
      expect(screen.queryByRole('status')).toBeNull();
      await act(async () => {
        server.dropStream();
      });
      expect(screen.queryByRole('status')).toBeNull();
      await act(async () => {
        vi.advanceTimersByTime(SILENT_DROP_MS);
      });
      expect(screen.getByRole('status')).toHaveTextContent(/^Reconnecting…$/);
      expect(screen.getByRole('status')).toHaveAttribute('data-tone', 'quiet');
      await act(async () => {
        vi.advanceTimersByTime(STALE_DROP_MS - SILENT_DROP_MS);
      });
      expect(screen.getByRole('status')).toHaveTextContent(
        'Reconnecting… showing the last known state',
      );
      expect(screen.getByRole('status')).not.toHaveAttribute('data-tone');
      expect(screen.getAllByTestId('task-node').length).toBeGreaterThan(0);
      await act(async () => {
        server.openStreams();
      });
      expect(screen.queryByRole('status')).toBeNull();
      // The next drop starts quiet, and a short one never shows.
      await act(async () => {
        server.dropStream();
      });
      await act(async () => {
        vi.advanceTimersByTime(SILENT_DROP_MS / 2);
      });
      expect(screen.queryByRole('status')).toBeNull();
      await act(async () => {
        server.openStreams();
      });
      expect(screen.queryByRole('status')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
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
          name: 'monolith',
          idPrefix: '',
          reachable: false,
          since: '2026-06-11T09:05:00Z',
          socket: '/x/agent-daemon.sock',
          usage: null,
          codexUsage: null,
          remoteHosts: [],
        };
      });
    });
    const banner = await screen.findByRole('status');
    expect(banner).toHaveTextContent('Agent host unreachable since');
    expect(banner).toHaveTextContent('showing the last known agents');
    expect(banner).toHaveTextContent('mael self restart');
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
