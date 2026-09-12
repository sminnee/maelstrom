import { deskIdForTask } from './protocol/deskId';
import { describe, expect, it } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { act } from 'react';
import userEvent from '@testing-library/user-event';
import { UNALLOCATED } from './selectors/graph';
import { askQuestion, chipCount, nodeState } from './test/appHelpers';
import { clickNode, renderApp } from './test/renderApp';
import { seedWorld } from './test/seedWorld';

describe('App', () => {
  it('renders the app title', async () => {
    await renderApp();
    expect(screen.getByRole('heading', { name: 'maelstrom' })).toBeInTheDocument();
  });

  it('renders one node per task on the desk, and one per free agent', async () => {
    await renderApp();
    const world = seedWorld().world;
    const tasks = Object.values(world.tasks)
      .filter((t) => t.status !== 'template' && deskIdForTask(t.id) in world.desk)
      .map((t) => t.id);
    const free = Object.values(world.agents)
      .filter((a) => !a.taskId)
      .map((a) => a.id);
    const rendered = (await screen.findAllByTestId('task-node'))
      .map((n) => n.getAttribute('data-task-id'))
      .sort();
    expect(rendered).toEqual([...tasks, ...free].sort());
  });

  it('dismisses a free agent from its card, once the agent has stopped', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    clickNode('f2c6a9d4');
    const card = screen.getByRole('dialog', { name: 'bravo · feat/task-index' });

    expect(within(card).getByRole('button', { name: 'Dismiss' })).toBeDisabled();

    server.change({ kind: 'agent', ids: ['f2c6a9d4'] }, (w) => {
      w.agents['f2c6a9d4'] = { ...w.agents['f2c6a9d4']!, state: 'exited', exitCode: 0 };
    });
    await waitFor(() =>
      expect(within(card).getByRole('button', { name: 'Dismiss' })).toBeEnabled(),
    );

    expect(document.querySelector('[data-task-id="f2c6a9d4"]')).toBeInTheDocument();
    await user.click(within(card).getByRole('button', { name: 'Dismiss' }));
    await waitFor(() =>
      expect(document.querySelector('[data-task-id="f2c6a9d4"]')).not.toBeInTheDocument(),
    );
  });

  it('removes a task from the desk from its own card', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('NORT-9.1');
    const card = screen.getByRole('dialog', { name: 'Watch the migration PR' });

    await user.click(within(card).getByRole('button', { name: 'Remove from desk' }));
    await waitFor(() =>
      expect(document.querySelector('[data-task-id="NORT-9.1"]')).not.toBeInTheDocument(),
    );
  });

  it('offers no removal on a task whose agent is live, since the node draws on regardless', async () => {
    await renderApp();
    clickNode('NORT-9');
    const card = screen.getByRole('dialog', { name: 'Migrate to Postgres 16' });

    // Terminate proves the footer rendered, so the absence below is the guard
    // at work rather than a card that drew nothing.
    expect(within(card).getByRole('button', { name: 'Terminate' })).toBeInTheDocument();
    expect(
      within(card).queryByRole('button', { name: 'Remove from desk' }),
    ).not.toBeInTheDocument();
  });

  it('reads the PR number and its state in the collapsed node identity', async () => {
    await renderApp();
    const node = document.querySelector('[data-task-id="NORT-12"]') as HTMLElement;
    expect(node).toHaveTextContent('#118');
    // The chip carries the state as a colour, so the board reads at a glance;
    // the name carries it in words, so colour is never the only channel.
    const chip = within(node).getByRole('link', { name: 'PR #118, CI running' });
    expect(chip).toHaveAttribute('data-tone', 'busy');
    // A task on a worktree with no PR says nothing.
    expect(document.querySelector('[data-task-id="NORT-9"]')).not.toHaveTextContent('#118');
  });

  it('reads a draft PR as a draft on the chip, whatever its checks say', async () => {
    const { server } = await renderApp();
    act(() => {
      server.change({ kind: 'worktree', ids: ['northwind-delta'] }, (w) => {
        w.worktrees['northwind-delta'] = {
          ...w.worktrees['northwind-delta']!,
          prState: 'ci-failed',
          prDraft: true,
        };
      });
    });
    await waitFor(() => {
      const node = document.querySelector('[data-task-id="NORT-12"]') as HTMLElement;
      // Draft wins, so the chip must not read as the failure underneath it.
      expect(within(node).getByRole('link', { name: 'PR #118, draft' })).toHaveAttribute(
        'data-tone',
        'quiet',
      );
    });
  });

  it('follows the PR state on the collapsed node as CI finishes', async () => {
    const { server } = await renderApp();
    act(() => {
      server.change({ kind: 'worktree', ids: ['northwind-delta'] }, (w) => {
        w.worktrees['northwind-delta'] = {
          ...w.worktrees['northwind-delta']!,
          prState: 'ci-failed',
        };
      });
    });
    await waitFor(() => {
      const node = document.querySelector('[data-task-id="NORT-12"]') as HTMLElement;
      expect(within(node).getByRole('link', { name: 'PR #118, CI failed' })).toHaveAttribute(
        'data-tone',
        'bad',
      );
    });
  });

  it('keeps every identity field on an expanded card, cost included', async () => {
    await renderApp();
    clickNode('NORT-12');
    const card = screen.getByRole('dialog', { name: 'Rotate auth tokens' });

    // jsdom applies no `text-overflow`, so this passes on the old CSS too: it
    // guards that every field reaches the line, not that the line wraps. The
    // browser check is in web/DESIGN.md, "Node Card".
    expect(within(card).getByTestId('node-meta')).toHaveTextContent(
      'feat/rotate-auth-tokens-for-every-service · delta · opus · normal · $0.66',
    );
  });

  it('draws a free agent once, named by the worktree it runs in', async () => {
    await renderApp();
    const node = document.querySelector('[data-task-id="f2c6a9d4"]');
    expect(node).toBeInTheDocument();
    expect(node).toHaveTextContent('bravo · feat/task-index');
    // The agent is not linked to a task, so no task node stands for it too.
    expect(document.querySelectorAll('[data-task-id="f2c6a9d4"]')).toHaveLength(1);
  });
});

describe('change notices', () => {
  it('a notice moves a working node into needs-attention with no reload', async () => {
    const { server } = await renderApp();
    expect(nodeState('NORT-9')).toBe('working');
    askQuestion(server);
    await waitFor(() => expect(nodeState('NORT-9')).toBe('needs-attention'));
    expect(chipCount()).toBe(4);
  });

  it('a notice for a task the world no longer holds takes its node away', async () => {
    const { server } = await renderApp();
    expect(document.querySelector('[data-task-id="NORT-9.1"]')).toBeInTheDocument();
    server.change({ kind: 'task', ids: ['NORT-9.1'] }, (w) => {
      delete w.tasks['NORT-9.1'];
    });
    await waitFor(() =>
      expect(document.querySelector('[data-task-id="NORT-9.1"]')).not.toBeInTheDocument(),
    );
  });
});

describe('grouping and filters', () => {
  it('filtering by branch removes the nodes of other branches', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.selectOptions(screen.getByLabelText('Branch'), 'northwind/feat/orders');
    const nodes = [...document.querySelectorAll('[data-testid="task-node"]')];
    expect(nodes.map((n) => n.getAttribute('data-task-id')).sort()).toEqual(['NORT-7', 'NORT-7.1']);
  });

  it('grouping by branch shows one group per branch, and by none shows no groups', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.selectOptions(screen.getByLabelText('Group by'), 'branch');
    const groups = () => document.querySelectorAll('[data-testid="group-node"]');
    const world = seedWorld().world;
    const branches = new Set(
      Object.values(world.tasks)
        .filter((t) => deskIdForTask(t.id) in world.desk)
        .map((t) => t.branch),
    );
    expect(groups()).toHaveLength(branches.size);
    await user.selectOptions(screen.getByLabelText('Group by'), 'none');
    expect(groups()).toHaveLength(0);
    expect(screen.getAllByTestId('task-node').length).toBeGreaterThan(0);
  });

  it('labels the progress zones the board uses, whatever it groups by', async () => {
    const user = userEvent.setup();
    await renderApp();
    const labels = () =>
      [...document.querySelectorAll('[data-testid="zone-label"]')].map((el) => el.textContent);
    // The desk holds no done task, so that zone collapses and draws no label.
    expect(labels()).toEqual(['Running', 'Not started']);
    // One strip for the whole board, so a board with no lanes still has it.
    await user.selectOptions(screen.getByLabelText('Group by'), 'none');
    expect(labels()).toEqual(['Running', 'Not started']);
  });

  // A collapsed zone holds no column, so a label for it would sit on top of
  // the next zone's rather than over anything of its own.
  it('draws no label for a zone no lane uses', async () => {
    const user = userEvent.setup();
    await renderApp();
    // One branch whose every task is queued: nothing is done, nothing runs.
    await user.selectOptions(screen.getByLabelText('Branch'), 'maelstrom/feat/orchestrator-ui');
    const labels = [...document.querySelectorAll('[data-testid="zone-label"]')];
    expect(labels.map((el) => el.getAttribute('data-zone'))).not.toContain('done');
    const lefts = labels.map((el) => (el as HTMLElement).style.left);
    expect(new Set(lefts).size).toBe(lefts.length);
  });

  it('grouping by worktree draws a lane per open worktree, empty ones included', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.selectOptions(screen.getByLabelText('Group by'), 'worktree');
    const open = Object.values(seedWorld().world.worktrees).filter((w) => !w.isClosed);
    const lanes = () => [...document.querySelectorAll('[data-testid="group-node"]')];
    // Exactly the open worktrees plus Unallocated: a closed worktree draws no
    // lane, and nothing draws twice.
    expect(new Set(lanes().map((l) => l.getAttribute('data-group-id')))).toEqual(
      new Set([...open.map((w) => w.id), UNALLOCATED]),
    );
    // `_main` never closes, so its lane is offered no button.
    const main = document.querySelector('[data-group-id="_main"]')!;
    expect(within(main as HTMLElement).queryByRole('button')).not.toBeInTheDocument();
  });

  it('a worktree lane closes its worktree, and reports a refusal on the button', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.selectOptions(screen.getByLabelText('Group by'), 'worktree');

    // northwind-alpha is clean, so the close goes through and its lane leaves.
    // fireEvent, not user-event: a canvas mousedown reaches React Flow's
    // d3-zoom, which jsdom cannot run — see clickNode in test/renderApp.tsx.
    const clean = document.querySelector('[data-group-id="northwind-alpha"]')!;
    fireEvent.click(within(clean as HTMLElement).getByRole('button', { name: 'Close' }));
    await waitFor(() =>
      expect(document.querySelector('[data-group-id="northwind-alpha"]')).not.toBeInTheDocument(),
    );

    // maelstrom-bravo has 2 unmerged commits, so the close is refused and the
    // button says so with the server's own words. The lane stays.
    const unmerged = document.querySelector('[data-group-id="maelstrom-bravo"]')!;
    const button = within(unmerged as HTMLElement).getByRole('button', { name: 'Close' });
    fireEvent.click(button);
    await waitFor(() =>
      expect(within(unmerged as HTMLElement).getByRole('button')).toHaveTextContent('Failed'),
    );
    expect(within(unmerged as HTMLElement).getByRole('button')).toHaveAttribute(
      'title',
      expect.stringContaining('not merged to origin/main'),
    );
    expect(document.querySelector('[data-group-id="maelstrom-bravo"]')).toBeInTheDocument();
  });
});
