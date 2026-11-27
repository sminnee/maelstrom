import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { makeAgent } from './fake/fixtures';
import type { FakeServer } from './fake/fakeServer';
import { paneItem } from './test/appHelpers';
import { renderApp } from './test/renderApp';

describe('the Foreman pane', () => {
  const pane = () => within(screen.getByTestId('foreman-pane'));
  const foremanPosts = (server: FakeServer) =>
    server.requests.filter((r) => r.path === '/api/foreman' && r.method === 'POST');
  const foremanIds = (server: FakeServer) =>
    Object.values(server.world.agents)
      .filter((a) => a.foreman)
      .map((a) => a.id);

  it('starts the foreman, then shows its conversation and a message box, off the canvas', async () => {
    const user = userEvent.setup();
    const { server, router } = await renderApp();
    await user.click(paneItem('Chat'));
    expect(router.state.location.pathname).toBe('/foreman');

    await user.click(pane().getByRole('button', { name: 'Start the foreman' }));
    await waitFor(() =>
      expect(pane().getByRole('textbox', { name: 'Message to agent' })).toBeInTheDocument(),
    );
    expect(foremanPosts(server)).toHaveLength(1);
    const [id] = foremanIds(server);
    expect(id).toBeDefined();
    // The desk shares the screen, and draws no node for the foreman.
    expect(document.querySelector(`[data-task-id="${id}"]`)).toBeNull();
    expect(pane().queryByRole('button', { name: 'Start the foreman' })).toBeNull();
  });

  it('resumes a foreman that has exited, so its conversation goes on', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp({ url: '/foreman' });
    server.change({ kind: 'agent', ids: ['fm1'] }, (w) => {
      w.agents['fm1'] = makeAgent({
        id: 'fm1',
        state: 'exited',
        taskId: '',
        project: '',
        worktreeId: '',
        foreman: true,
      });
    });

    await user.click(await pane().findByRole('button', { name: 'Resume the foreman' }));
    await waitFor(() => expect(server.world.agents['fm1']!.state).toBe('idle'));
    expect(foremanIds(server)).toEqual(['fm1']);
    await waitFor(() =>
      expect(pane().queryByRole('button', { name: 'Resume the foreman' })).toBeNull(),
    );
  });
});

describe('voice in the Foreman pane', () => {
  it('shows the Voice toggle, and voice in a session tab turns the foreman voice off', async () => {
    const user = userEvent.setup();
    // The Foreman pane on the left, and a task agent's session in the panel
    // beside it: both views on screen at once.
    await renderApp({ url: '/foreman?panel=session/d9a4c7f1' });
    await user.keyboard('{Shift>}');
    await user.click(paneItem('Chat'));
    await user.keyboard('{/Shift}');
    await user.click(paneItem('Tabs'));
    const pane = () => within(screen.getByTestId('foreman-pane'));
    await user.click(await pane().findByRole('button', { name: 'Start the foreman' }));
    const foremanVoice = await pane().findByRole('button', { name: 'Voice' });
    await user.click(foremanVoice);
    expect(foremanVoice).toHaveAttribute('aria-pressed', 'true');

    // One mic and one speaker: a second session view takes voice over.
    const tab = within(screen.getByTestId('panel'));
    await user.click(await tab.findByRole('button', { name: 'Voice' }));
    expect(tab.getByRole('button', { name: 'Voice' })).toHaveAttribute('aria-pressed', 'true');
    expect(foremanVoice.isConnected).toBe(true);
    expect(foremanVoice).toHaveAttribute('aria-pressed', 'false');
  });
});
