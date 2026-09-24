import type { DeskBody } from './api/desk';
import { keys } from './api/keys';
import { deskIdForTask } from './protocol/deskId';
import { describe, expect, it } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { act } from 'react';
import userEvent from '@testing-library/user-event';
import { UNALLOCATED } from './selectors/graph';
import { askQuestion, chipCount, commandsSince, nodeState } from './test/appHelpers';
import { clickNode, renderApp } from './test/renderApp';
import { seedWorld } from './test/seedWorld';

/** The labels of the open menu in `card`, in order. */
function menuLabels(card: HTMLElement): (string | null)[] {
  return within(card)
    .getAllByRole('menuitem')
    .map((i) => i.getAttribute('aria-label'));
}

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

  // The wire itself never draws in jsdom, but a Handle does: this is what
  // `nodesConnectable` buys, and flipping it back would pass unnoticed.
  it('draws both wire anchors on a task node', async () => {
    await renderApp();
    const node = (await screen.findAllByTestId('task-node'))[0]!;
    const handles = node.querySelectorAll('.react-flow__handle');
    expect(handles).toHaveLength(2);
    expect(node.querySelector('.react-flow__handle-left')).toBeInTheDocument();
    expect(node.querySelector('.react-flow__handle-right')).toBeInTheDocument();
    // React Flow marks a handle connectable only when the board allows it.
    expect(handles[0]).toHaveClass('connectable');
  });

  it('offers Terminate while the agent is live, and Dismiss once it has stopped', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    clickNode('f2c6a9d4');
    const card = screen.getByRole('dialog', { name: 'bravo · feat/task-index' });

    // Live, the card's end-of-work control terminates.
    expect(within(card).getByRole('button', { name: 'Terminate' })).toBeInTheDocument();

    server.change({ kind: 'agent', ids: ['f2c6a9d4'] }, (w) => {
      w.agents['f2c6a9d4'] = { ...w.agents['f2c6a9d4']!, state: 'exited', exitCode: 0 };
    });
    await waitFor(() =>
      expect(within(card).getByRole('button', { name: 'Dismiss' })).toBeInTheDocument(),
    );
    expect(within(card).getByRole('button', { name: 'Resume' })).toBeInTheDocument();
    await user.click(within(card).getByRole('button', { name: 'More actions' }));
    expect(menuLabels(card)).toEqual(['Dismiss', 'Dismiss & close bravo']);
    // c3e8f1b5 still runs in maelstrom-bravo.
    expect(within(card).getByRole('menuitem', { name: 'Dismiss & close bravo' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    await user.keyboard('{Escape}');

    expect(document.querySelector('[data-task-id="f2c6a9d4"]')).toBeInTheDocument();
    await user.click(within(card).getByRole('button', { name: 'Dismiss' }));
    await waitFor(() =>
      expect(document.querySelector('[data-task-id="f2c6a9d4"]')).not.toBeInTheDocument(),
    );
  });

  it('dismisses a task with no agent from its own card', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    clickNode('NORT-9.1');
    const card = screen.getByRole('dialog', { name: 'Watch the migration PR' });

    const before = server.requests.length;
    await user.click(within(card).getByRole('button', { name: 'Dismiss' }));
    await waitFor(() =>
      expect(document.querySelector('[data-task-id="NORT-9.1"]')).not.toBeInTheDocument(),
    );
    expect(commandsSince(server, before)).toEqual(['DELETE /api/desk/task:NORT-9.1']);
  });

  it('closes the worktree of a stopped agent, then dismisses', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    server.change({ kind: 'agent', ids: ['a1f3c9e2'] }, (w) => {
      w.agents['a1f3c9e2'] = {
        ...w.agents['a1f3c9e2']!,
        state: 'exited',
        exitCode: 0,
        pendingRequestIds: [],
      };
    });
    clickNode('NORT-7');
    const card = screen.getByRole('dialog', { name: 'Plan the order export' });

    await within(card).findByRole('button', { name: 'Dismiss' });
    await user.click(within(card).getByRole('button', { name: 'More actions' }));
    const before = server.requests.length;
    await user.click(within(card).getByRole('menuitem', { name: 'Dismiss & close alpha' }));
    await waitFor(() =>
      expect(document.querySelector('[data-task-id="NORT-7"]')).not.toBeInTheDocument(),
    );
    expect(commandsSince(server, before)).toEqual([
      'POST /api/worktrees/northwind-alpha/close',
      'DELETE /api/desk/task:NORT-7',
    ]);
  });

  it('draws a plain Dismiss, with no menu, on a task with no worktree to close', async () => {
    await renderApp();
    clickNode('NORT-15');
    const card = screen.getByRole('dialog', { name: 'Shape the reporting module' });

    expect(within(card).getByRole('button', { name: 'Dismiss' })).toBeInTheDocument();
    expect(within(card).queryByRole('button', { name: 'More actions' })).not.toBeInTheDocument();
  });

  it('draws a plain Dismiss, with no menu, when the worktree is closed already', async () => {
    const { server } = await renderApp();
    clickNode('f2c6a9d4');
    const card = screen.getByRole('dialog', { name: 'bravo · feat/task-index' });
    server.change({ kind: 'agent', ids: ['f2c6a9d4'] }, (w) => {
      w.agents['f2c6a9d4'] = { ...w.agents['f2c6a9d4']!, state: 'exited', exitCode: 0 };
    });
    server.change({ kind: 'worktree', ids: ['maelstrom-bravo'] }, (w) => {
      w.worktrees['maelstrom-bravo'] = { ...w.worktrees['maelstrom-bravo']!, isClosed: true };
    });

    await waitFor(() =>
      expect(within(card).getByRole('button', { name: 'Dismiss' })).toBeInTheDocument(),
    );
    expect(within(card).queryByRole('button', { name: 'More actions' })).not.toBeInTheDocument();
  });

  it('resumes a terminated agent from the card that terminated it, and terminate sends only a stop', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    clickNode('NORT-9');
    const card = screen.getByRole('dialog', { name: 'Migrate to Postgres 16' });

    const before = server.requests.length;
    await user.click(within(card).getByRole('button', { name: 'Terminate' }));
    await waitFor(() =>
      expect(within(card).getByRole('button', { name: 'Resume' })).toBeInTheDocument(),
    );
    expect(commandsSince(server, before)).toEqual(['POST /api/agents/d9a4c7f1/stop']);
    expect(within(card).queryByRole('button', { name: 'Terminate' })).not.toBeInTheDocument();

    await user.click(within(card).getByRole('button', { name: 'Resume' }));
    await waitFor(() =>
      expect(within(card).getByRole('button', { name: 'Terminate' })).toBeInTheDocument(),
    );
    expect(within(card).queryByRole('button', { name: 'Resume' })).not.toBeInTheDocument();
  });

  it('terminates and dismisses a live agent in one choice', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    clickNode('NORT-9');
    const card = screen.getByRole('dialog', { name: 'Migrate to Postgres 16' });

    await user.click(within(card).getByRole('button', { name: 'More actions' }));
    expect(menuLabels(card)).toEqual([
      'Terminate',
      'Terminate & dismiss',
      'Terminate, dismiss & close bravo',
    ]);
    const before = server.requests.length;
    await user.click(within(card).getByRole('menuitem', { name: 'Terminate & dismiss' }));
    await waitFor(() =>
      expect(document.querySelector('[data-task-id="NORT-9"]')).not.toBeInTheDocument(),
    );
    expect(commandsSince(server, before)).toEqual([
      'POST /api/agents/d9a4c7f1/stop',
      'DELETE /api/desk/task:NORT-9',
    ]);
    expect(
      screen.queryByRole('dialog', { name: 'Migrate to Postgres 16' }),
    ).not.toBeInTheDocument();
  });

  it('closes the worktree, then dismisses, with no stop of its own', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    clickNode('NORT-12');
    const card = screen.getByRole('dialog', { name: 'Rotate auth tokens' });

    await user.click(within(card).getByRole('button', { name: 'More actions' }));
    const before = server.requests.length;
    await user.click(
      within(card).getByRole('menuitem', { name: 'Terminate, dismiss & close delta' }),
    );
    await waitFor(() =>
      expect(document.querySelector('[data-task-id="NORT-12"]')).not.toBeInTheDocument(),
    );
    // The close stops the agent itself, so the card sends no stop.
    expect(commandsSince(server, before)).toEqual([
      'POST /api/worktrees/northwind-delta/close',
      'DELETE /api/desk/task:NORT-12',
    ]);
  });

  it('leaves the node on the desk when the close refuses, and says why', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    clickNode('NORT-9');
    const card = screen.getByRole('dialog', { name: 'Migrate to Postgres 16' });

    await user.click(within(card).getByRole('button', { name: 'More actions' }));
    const close = within(card).getByRole('menuitem', { name: 'Terminate, dismiss & close bravo' });
    // The subagent d9a4c7f1.1 is live in the same worktree, and does not hold the close.
    expect(close).not.toHaveAttribute('aria-disabled');
    const before = server.requests.length;
    await user.click(close);
    const alert = await within(card).findByRole('alert');
    expect(alert.closest('button')).toHaveAttribute('title', 'Worktree has uncommitted changes');
    expect(commandsSince(server, before)).toEqual(['POST /api/worktrees/northwind-bravo/close']);
    expect(document.querySelector('[data-task-id="NORT-9"]')).toBeInTheDocument();
  });

  it('sends no dismiss when the stop fails', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    server.refuse(/POST \/api\/agents\/d9a4c7f1\/stop$/, { status: 409, code: 'invalid' });
    clickNode('NORT-9');
    const card = screen.getByRole('dialog', { name: 'Migrate to Postgres 16' });

    await user.click(within(card).getByRole('button', { name: 'More actions' }));
    const before = server.requests.length;
    await user.click(within(card).getByRole('menuitem', { name: 'Terminate & dismiss' }));
    await within(card).findByRole('alert');
    expect(commandsSince(server, before)).toEqual(['POST /api/agents/d9a4c7f1/stop']);
    expect(document.querySelector('[data-task-id="NORT-9"]')).toBeInTheDocument();
  });

  it('terminates a live node with no desk entry, and has nothing to dismiss', async () => {
    const user = userEvent.setup();
    const { server, queryClient } = await renderApp();
    // The task list row removes a live task from the desk; its node draws on.
    server.change({ kind: 'desk', ids: ['task:NORT-9'] }, (w) => {
      delete w.desk['task:NORT-9'];
    });
    await waitFor(() =>
      expect(queryClient.getQueryData<DeskBody>(keys.desk())?.desk.map((e) => e.id)).not.toContain(
        'task:NORT-9',
      ),
    );
    clickNode('NORT-9');
    const card = screen.getByRole('dialog', { name: 'Migrate to Postgres 16' });

    await user.click(within(card).getByRole('button', { name: 'More actions' }));
    const before = server.requests.length;
    await user.click(within(card).getByRole('menuitem', { name: 'Terminate & dismiss' }));
    await waitFor(() =>
      expect(document.querySelector('[data-task-id="NORT-9"]')).not.toBeInTheDocument(),
    );
    expect(commandsSince(server, before)).toEqual(['POST /api/agents/d9a4c7f1/stop']);
  });

  it('holds the close while another agent still runs in the worktree', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('f2c6a9d4');
    const card = screen.getByRole('dialog', { name: 'bravo · feat/task-index' });

    await user.click(within(card).getByRole('button', { name: 'More actions' }));
    const close = within(card).getByRole('menuitem', {
      name: 'Terminate, dismiss & close bravo',
    });
    expect(close).toHaveAttribute('aria-disabled', 'true');
    expect(close).toHaveAccessibleDescription('1 other agent still running in bravo');
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
    // browser check is in orchestrator-ui/DESIGN.md, "Node Card".
    expect(within(card).getByTestId('node-meta')).toHaveTextContent(
      'feat/rotate-auth-tokens-for-every-service · delta · claude:opus · normal · $0.66',
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
  it('uses one Project and Branch filter in Desk and Tasks', async () => {
    const user = userEvent.setup();
    await renderApp();

    await user.selectOptions(screen.getByLabelText('Project'), 'northwind');
    await user.selectOptions(screen.getByLabelText('Branch'), 'northwind/feat/orders');
    await user.click(screen.getByRole('button', { name: 'Tasks' }));

    expect(screen.getByLabelText('Project')).toHaveValue('northwind');
    expect(screen.getByLabelText('Branch')).toHaveValue('northwind/feat/orders');
    expect(
      within(screen.getByTestId('task-list'))
        .getAllByRole('row')
        .map((row) => row.textContent),
    ).toEqual(expect.arrayContaining([expect.stringContaining('NORT-7')]));
    expect(screen.getByRole('button', { name: 'Tasks' })).toHaveAttribute('aria-pressed', 'true');

    await user.click(screen.getByRole('button', { name: 'Desk' }));
    expect(screen.getByLabelText('Project')).toHaveValue('northwind');
    expect(screen.getByLabelText('Branch')).toHaveValue('northwind/feat/orders');
  });

  it('filters Desk nodes by agent status', async () => {
    const user = userEvent.setup();
    await renderApp();

    await user.selectOptions(screen.getByLabelText('Agent status'), 'planned');

    expect(
      screen.getAllByTestId('task-node').map((node) => node.getAttribute('data-task-id')),
    ).toEqual(expect.arrayContaining(['NORT-15']));
    expect(document.querySelector('[data-task-id="NORT-9"]')).not.toBeInTheDocument();
  });

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
