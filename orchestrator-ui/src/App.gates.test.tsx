import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import { act } from 'react';
import userEvent from '@testing-library/user-event';
import { renderApp } from './test/renderApp';

describe('the gates chip', () => {
  it('counts the running gate jobs over every job', async () => {
    await renderApp();
    // The seed holds two running jobs and one queued: the gap is the queue.
    const chip = await screen.findByLabelText('2 gate jobs running, 1 queued. Click to list them.');
    expect(chip).toHaveTextContent('2/3');
  });

  it('is absent when no gate job runs or waits', async () => {
    const { server } = await renderApp();
    await screen.findByLabelText(/gate jobs running/);
    await act(async () => {
      server.change({ kind: 'gateJob', ids: [] }, (world) => {
        world.gateJobs = {};
      });
    });
    await waitFor(() => expect(screen.queryByLabelText(/gate jobs running/)).toBeNull());
  });

  it('lists the running jobs, then the queued jobs, with their gates', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.click(await screen.findByLabelText(/gate jobs running/));
    const dialog = await screen.findByRole('dialog', { name: 'Gate jobs' });

    const running = within(within(dialog).getByRole('region', { name: 'Running' }));
    const queued = within(within(dialog).getByRole('region', { name: 'Queued' }));
    // Oldest first. A worktree the world knows is named by project and NATO name.
    const rows = running.getAllByRole('listitem');
    expect(rows.map((row) => row.firstElementChild!.firstElementChild!.textContent)).toEqual([
      'northwind/alpha',
      'maelstrom/bravo',
    ]);
    const first = within(rows[0]!);
    expect(first.getByText('lint')).toHaveAttribute('data-state', 'passed');
    expect(first.getByText('test')).toHaveAttribute('data-state', 'running');

    // One the world does not know keeps its path.
    const waiting = within(queued.getByRole('listitem'));
    expect(waiting.getByText('/Users/dev/Projects/askastro/askastro-bravo')).toBeInTheDocument();
    expect(waiting.getByText('queue 1 of 1')).toBeInTheDocument();
  });
});
