import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import { act } from 'react';
import userEvent from '@testing-library/user-event';
import { endTurn } from './fake/moves';
import { nodeState, unansweredCount } from './test/appHelpers';
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

  it('shows no usage for a job tangier took no sample of', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    await act(async () => {
      server.change({ kind: 'gateJob', ids: ['43'] }, (world) => {
        world.gateJobs['43']!.usage = null;
      });
    });
    await user.click(await screen.findByLabelText(/gate jobs running/));
    const dialog = await screen.findByRole('dialog', { name: 'Gate jobs' });
    const queued = within(within(dialog).getByRole('region', { name: 'Queued' }));
    expect(
      await within(queued.getByRole('listitem')).findByText('queue 1 of 1'),
    ).toBeInTheDocument();
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
      'maelstrom/alpha',
    ]);
    const first = within(rows[0]!);
    expect(first.getByText('lint')).toHaveAttribute('data-state', 'passed');
    expect(first.getByText('test')).toHaveAttribute('data-state', 'running');
    expect(first.getByText(/^started, .* ago · cpu 94% · mem 107M$/)).toBeInTheDocument();

    // One the world does not know keeps its path.
    const waiting = within(queued.getByRole('listitem'));
    expect(waiting.getByText('/Users/dev/Projects/askastro/askastro-bravo')).toBeInTheDocument();
    expect(waiting.getByText('queue 1 of 1 · cpu 0% · mem 4.0M')).toBeInTheDocument();
  });
});

describe('the gate lozenge', () => {
  const name = 'gate job 42: test running, open the gate jobs';

  it("names the running gate on each node of the job's worktree", async () => {
    await renderApp();
    // The chain on `feat/orders` shares the worktree, so each of its nodes shows the job.
    for (const chip of await screen.findAllByRole('button', { name })) {
      expect(chip).toHaveTextContent('test');
    }
  });

  it('opens the gate jobs on its job', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.click((await screen.findAllByRole('button', { name }))[0]!);
    const dialog = await screen.findByRole('dialog', { name: 'Gate jobs' });
    const marked = within(dialog)
      .getAllByRole('listitem')
      .filter((row) => row.getAttribute('aria-current') === 'true');
    expect(marked).toHaveLength(1);
    expect(marked[0]).toHaveTextContent('northwind/alpha');
  });

  it('goes when the worktree has no gate job', async () => {
    const { server } = await renderApp();
    await screen.findAllByRole('button', { name });
    await act(async () => {
      server.change({ kind: 'worktree', ids: ['northwind-alpha'] }, (world) => {
        world.worktrees['northwind-alpha']!.gate = null;
      });
    });
    await waitFor(() => expect(screen.queryAllByRole('button', { name })).toEqual([]));
  });

  it('turns an agent that ended its turn on the gate from unanswered to working', async () => {
    const { server } = await renderApp();
    act(() => endTurn(server));
    await waitFor(() => expect(nodeState('MAEL-40.1')).toBe('unanswered'));
    expect(unansweredCount()).toBe(1);

    act(() => {
      server.change({ kind: 'worktree', ids: ['maelstrom-bravo'] }, (world) => {
        world.worktrees['maelstrom-bravo']!.gate = {
          jobId: '44',
          state: 'running',
          gate: 'test',
          queuePosition: null,
        };
      });
    });
    await waitFor(() => expect(nodeState('MAEL-40.1')).toBe('working'));
    expect(unansweredCount()).toBe(0);
    expect(document.querySelector('[data-task-id="MAEL-40.1"]')).toHaveTextContent('Gate running');
  });
});
