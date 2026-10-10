import type { DeskBody } from './api/desk';
import { keys } from './api/keys';
import { deskIdForTask } from './protocol/deskId';
import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import { act } from 'react';
import userEvent from '@testing-library/user-event';
import { askQuestion } from './fake/moves';
import {
  chipCount,
  commandsSince,
  exitAgent,
  isShowing,
  nodeState,
  paneItem,
} from './test/appHelpers';
import { clickNode, renderApp } from './test/renderApp';
import { seedWorld } from './fake/seedWorld';

/**
 * The card's own commands. A follows row has an Off desk button of its own, so
 * the card's end-of-work control is found here, not in the whole card.
 */
const commands = (card: HTMLElement) => within(within(card).getByTestId('node-commands'));

/** The labels of the open menu in `card`, in order. */
function menuLabels(card: HTMLElement): (string | null)[] {
  return within(card)
    .getAllByRole('menuitem')
    .map((i) => i.getAttribute('aria-label'));
}

/** The label of the end-of-work item that Dismiss runs, read from its menu. */
async function dismissRuns(
  user: ReturnType<typeof userEvent.setup>,
  card: HTMLElement,
): Promise<string | null> {
  // The env control in the worktree area has a menu of the same name.
  await user.click(commands(card).getByRole('button', { name: 'More actions' }));
  const item = within(card)
    .getAllByRole('menuitem')
    .find((i) => i.hasAttribute('data-default'));
  await user.keyboard('{Escape}');
  return item?.getAttribute('aria-label') ?? null;
}

describe('App', () => {
  it('renders the app title beside the mark', async () => {
    await renderApp();
    const title = screen.getByRole('heading', { name: 'maelstrom' });
    expect(title.querySelector('img')).toHaveAttribute('src', '/logo.svg');
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

  it('dismisses a live agent that shares its worktree with a stop, and sends no close', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    clickNode('f2c6a9d4');
    const card = screen.getByRole('dialog', { name: 'bravo · feat/task-index' });

    // c3e8f1b5 still runs in maelstrom-bravo, so Dismiss does not close it.
    expect(await dismissRuns(user, card)).toBe('…and take off desk');
    const before = server.requests.length;
    await user.click(commands(card).getByRole('button', { name: 'Dismiss' }));
    await waitFor(() =>
      expect(document.querySelector('[data-task-id="f2c6a9d4"]')).not.toBeInTheDocument(),
    );
    expect(commandsSince(server, before)).toEqual([
      'POST /api/agents/f2c6a9d4/stop',
      'DELETE /api/desk/agent:f2c6a9d4',
    ]);
    expect(
      screen.queryByRole('dialog', { name: 'bravo · feat/task-index' }),
    ).not.toBeInTheDocument();
  });

  it('dismisses a stopped agent that shares its worktree with Off desk alone', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    clickNode('f2c6a9d4');
    const card = screen.getByRole('dialog', { name: 'bravo · feat/task-index' });

    server.change({ kind: 'agent', ids: ['f2c6a9d4'] }, (w) => {
      w.agents['f2c6a9d4'] = { ...w.agents['f2c6a9d4']!, state: 'exited', exitCode: 0 };
    });
    await waitFor(() =>
      expect(within(card).getByRole('button', { name: 'Resume' })).toBeInTheDocument(),
    );
    await user.click(within(card).getByRole('button', { name: 'More actions' }));
    expect(menuLabels(card)).toEqual([
      'Off desk',
      '…and close bravo',
      '…shelving the branch',
      '…or trashing the branch',
      '…or ignoring the branch',
    ]);
    expect(within(card).getByRole('menuitem', { name: 'Off desk' })).toHaveAttribute(
      'data-default',
    );
    // c3e8f1b5 still runs in maelstrom-bravo.
    for (const name of [
      '…and close bravo',
      '…shelving the branch',
      '…or trashing the branch',
      '…or ignoring the branch',
    ]) {
      expect(within(card).getByRole('menuitem', { name })).toHaveAttribute('aria-disabled', 'true');
    }
    await user.keyboard('{Escape}');

    expect(document.querySelector('[data-task-id="f2c6a9d4"]')).toBeInTheDocument();
    const before = server.requests.length;
    await user.click(commands(card).getByRole('button', { name: 'Dismiss' }));
    await waitFor(() =>
      expect(document.querySelector('[data-task-id="f2c6a9d4"]')).not.toBeInTheDocument(),
    );
    expect(commandsSince(server, before)).toEqual(['DELETE /api/desk/agent:f2c6a9d4']);
  });

  it('takes a task with no agent off the desk from its own card', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    clickNode('NORT-9.1');
    const card = screen.getByRole('dialog', { name: 'Watch the migration PR' });

    const before = server.requests.length;
    await user.click(commands(card).getByRole('button', { name: 'Dismiss' }));
    await waitFor(() =>
      expect(document.querySelector('[data-task-id="NORT-9.1"]')).not.toBeInTheDocument(),
    );
    expect(commandsSince(server, before)).toEqual(['DELETE /api/desk/task:NORT-9.1']);
  });

  it('dismisses a stopped agent alone in its worktree with a close, then takes it off the desk', async () => {
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

    await within(card).findByRole('button', { name: 'Resume' });
    expect(await dismissRuns(user, card)).toBe('…and close alpha');
    const before = server.requests.length;
    await user.click(commands(card).getByRole('button', { name: 'Dismiss' }));
    await waitFor(() =>
      expect(document.querySelector('[data-task-id="NORT-7"]')).not.toBeInTheDocument(),
    );
    expect(commandsSince(server, before)).toEqual([
      'POST /api/worktrees/northwind-alpha/close',
      'DELETE /api/desk/task:NORT-7',
    ]);
  });

  it('asks before it trashes a worktree, then trashes and takes it off the desk', async () => {
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
    await within(card).findByRole('button', { name: 'Resume' });
    const before = server.requests.length;

    // Picking it asks, and sends nothing.
    await user.click(within(card).getByRole('button', { name: 'More actions' }));
    await user.click(within(card).getByRole('menuitem', { name: '…or trashing the branch' }));
    const ask = within(card).getByRole('alertdialog', {
      name: 'Trash feat/orders? Its PR closes and the branch moves to trash/.',
    });
    await user.click(within(ask).getByRole('button', { name: 'Keep it' }));
    expect(commandsSince(server, before)).toEqual([]);

    await user.click(within(card).getByRole('button', { name: 'More actions' }));
    await user.click(within(card).getByRole('menuitem', { name: '…or trashing the branch' }));
    await user.click(within(card).getByRole('button', { name: 'Trash it' }));
    await waitFor(() =>
      expect(document.querySelector('[data-task-id="NORT-7"]')).not.toBeInTheDocument(),
    );
    expect(commandsSince(server, before)).toEqual([
      'POST /api/worktrees/northwind-alpha/trash',
      'DELETE /api/desk/task:NORT-7',
    ]);
  });

  it.each([
    [
      '…shelving the branch',
      'Shelve alpha? Work in progress is committed. Unmerged work gets a task to reopen it.',
      'Shelve it',
      'POST /api/worktrees/northwind-alpha/force-close',
    ],
    [
      '…or ignoring the branch',
      'Delete alpha? The checkout goes; the branch stays.',
      'Delete it',
      'DELETE /api/worktrees/northwind-alpha',
    ],
  ])(
    'asks before %s, then runs it and takes the node off the desk',
    async (item, question, answer, command) => {
      const user = userEvent.setup();
      const { server } = await renderApp();
      exitAgent(server, 'a1f3c9e2');
      clickNode('NORT-7');
      const card = screen.getByRole('dialog', { name: 'Plan the order export' });
      await within(card).findByRole('button', { name: 'Resume' });
      const before = server.requests.length;

      await user.click(within(card).getByRole('button', { name: 'More actions' }));
      await user.click(within(card).getByRole('menuitem', { name: item }));
      const ask = within(card).getByRole('alertdialog', { name: question });
      expect(commandsSince(server, before)).toEqual([]);
      await user.click(within(ask).getByRole('button', { name: answer }));
      await waitFor(() =>
        expect(document.querySelector('[data-task-id="NORT-7"]')).not.toBeInTheDocument(),
      );
      expect(commandsSince(server, before)).toEqual([command, 'DELETE /api/desk/task:NORT-7']);
    },
  );

  it('draws a plain Dismiss, with no menu, on a task with no worktree to close', async () => {
    await renderApp();
    clickNode('NORT-15');
    const card = screen.getByRole('dialog', { name: 'Shape the reporting module' });

    expect(commands(card).getByRole('button', { name: 'Dismiss' })).toBeInTheDocument();
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
      expect(within(card).queryByRole('button', { name: 'More actions' })).not.toBeInTheDocument(),
    );
    expect(commands(card).getByRole('button', { name: 'Dismiss' })).toBeInTheDocument();
  });

  it('resumes a terminated agent from the card that terminated it, and terminate sends only a stop', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    clickNode('NORT-9');
    const card = screen.getByRole('dialog', { name: 'Migrate to Postgres 16' });

    await user.click(within(card).getByRole('button', { name: 'More actions' }));
    const before = server.requests.length;
    await user.click(within(card).getByRole('menuitem', { name: 'Terminate' }));
    await waitFor(() =>
      expect(within(card).getByRole('button', { name: 'Resume' })).toBeInTheDocument(),
    );
    expect(commandsSince(server, before)).toEqual(['POST /api/agents/d9a4c7f1/stop']);

    await user.click(within(card).getByRole('button', { name: 'Resume' }));
    await waitFor(() =>
      expect(within(card).queryByRole('button', { name: 'Resume' })).not.toBeInTheDocument(),
    );
    // Live again, the control offers the live chains.
    await user.click(within(card).getByRole('button', { name: 'More actions' }));
    expect(menuLabels(card)).toEqual([
      'Terminate',
      '…and take off desk',
      '…and close bravo',
      '…shelving the branch',
      '…or trashing the branch',
      '…or ignoring the branch',
    ]);
  });

  it('dismisses a live agent alone in its worktree with a close, then takes it off the desk', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    clickNode('NORT-12');
    const card = screen.getByRole('dialog', { name: 'Rotate auth tokens' });

    expect(await dismissRuns(user, card)).toBe('…and close delta');
    const before = server.requests.length;
    await user.click(commands(card).getByRole('button', { name: 'Dismiss' }));
    await waitFor(() =>
      expect(document.querySelector('[data-task-id="NORT-12"]')).not.toBeInTheDocument(),
    );
    // The close stops the agent itself, so the card sends no stop.
    expect(commandsSince(server, before)).toEqual([
      'POST /api/worktrees/northwind-delta/close',
      'DELETE /api/desk/task:NORT-12',
    ]);
  });

  it('leaves the node on the desk when the close of a Dismiss refuses, and says why', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    clickNode('NORT-9');
    const card = screen.getByRole('dialog', { name: 'Migrate to Postgres 16' });

    // The subagent d9a4c7f1.1 is live in the same worktree, and does not hold the close.
    expect(await dismissRuns(user, card)).toBe('…and close bravo');
    const before = server.requests.length;
    await user.click(commands(card).getByRole('button', { name: 'Dismiss' }));
    const alert = await within(card).findByRole('alert');
    expect(alert.closest('button')).toHaveAttribute('title', 'Worktree has uncommitted changes');
    expect(commandsSince(server, before)).toEqual(['POST /api/worktrees/northwind-bravo/close']);
    expect(document.querySelector('[data-task-id="NORT-9"]')).toBeInTheDocument();
  });

  it('sends no Off desk when the stop fails', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    server.refuse(/POST \/api\/agents\/d9a4c7f1\/stop$/, { status: 409, code: 'invalid' });
    clickNode('NORT-9');
    const card = screen.getByRole('dialog', { name: 'Migrate to Postgres 16' });

    await user.click(within(card).getByRole('button', { name: 'More actions' }));
    const before = server.requests.length;
    await user.click(within(card).getByRole('menuitem', { name: '…and take off desk' }));
    await within(card).findByRole('alert');
    expect(commandsSince(server, before)).toEqual(['POST /api/agents/d9a4c7f1/stop']);
    expect(document.querySelector('[data-task-id="NORT-9"]')).toBeInTheDocument();
  });

  it('terminates a live node with no desk entry, and has nothing to take off the desk', async () => {
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
    await user.click(within(card).getByRole('menuitem', { name: '…and take off desk' }));
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
      name: '…and close bravo',
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

  it('draws no PR its task did not register, though its branch has one', async () => {
    const { server } = await renderApp();
    act(() => {
      server.change({ kind: 'task', ids: ['NORT-12'] }, (w) => {
        w.tasks['NORT-12'] = { ...w.tasks['NORT-12']!, prNumber: 0, prUrl: '' };
      });
    });
    await waitFor(() => {
      const node = document.querySelector('[data-task-id="NORT-12"]') as HTMLElement;
      expect(node).not.toHaveTextContent('#118');
    });
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
      /^claude:opus · normal · \$0\.66$/,
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
  it('puts a filter in the URL, and Back takes it off again', async () => {
    const user = userEvent.setup();
    const { router } = await renderApp();
    await user.selectOptions(screen.getByLabelText('Project'), 'northwind');
    await user.selectOptions(screen.getByLabelText('Agent status'), 'idle');
    expect(router.state.location.search).toBe('?project=northwind&agents=idle');
    await act(() => router.navigate(-1));
    expect(screen.getByLabelText('Agent status')).toHaveValue('all');
    expect(screen.getByLabelText('Project')).toHaveValue('northwind');
  });

  it('opens on the filters the URL names', async () => {
    await renderApp({ url: '/desk?project=maelstrom&branch=maelstrom/feat/orchestrator-ui' });
    expect(screen.getByLabelText('Project')).toHaveValue('maelstrom');
    expect(screen.getByLabelText('Branch')).toHaveValue('maelstrom/feat/orchestrator-ui');
  });

  it('uses one Project and Branch filter in Desk and Tasks', async () => {
    const user = userEvent.setup();
    await renderApp();

    await user.selectOptions(screen.getByLabelText('Project'), 'northwind');
    await user.selectOptions(screen.getByLabelText('Branch'), 'northwind/feat/orders');
    await user.click(paneItem('Tasks'));

    expect(screen.getByLabelText('Project')).toHaveValue('northwind');
    expect(screen.getByLabelText('Branch')).toHaveValue('northwind/feat/orders');
    expect(
      within(screen.getByTestId('task-list'))
        .getAllByRole('row')
        .map((row) => row.textContent),
    ).toEqual(expect.arrayContaining([expect.stringContaining('NORT-7')]));
    expect(isShowing('Tasks')).toBe(true);

    await user.click(paneItem('Desk'));
    expect(screen.getByLabelText('Project')).toHaveValue('northwind');
    expect(screen.getByLabelText('Branch')).toHaveValue('northwind/feat/orders');
  });

  it('keeps a branch picked on Tasks on the Desk, and otherwise offers only the desk work', async () => {
    const user = userEvent.setup();
    await renderApp();
    const values = (label: string) =>
      [...(screen.getByLabelText(label) as HTMLSelectElement).options].map((o) => o.value);

    // spike/graphql has no desk work, so only Tasks offers it.
    await user.click(paneItem('Tasks'));
    await user.selectOptions(screen.getByLabelText('Branch'), 'northwind/spike/graphql');
    await user.click(paneItem('Desk'));
    expect(screen.getByLabelText('Branch')).toHaveValue('northwind/spike/graphql');

    // Choosing a project, even "all", clears the branch, so nothing is kept.
    await user.selectOptions(screen.getByLabelText('Project'), '');
    expect(screen.getByLabelText('Branch')).toHaveValue('');
    // riverbend has no work at all.
    expect(values('Project')).toEqual(['', 'maelstrom', 'northwind']);
    expect(values('Branch')).toEqual([
      '',
      'maelstrom/feat/orchestrator-ui',
      'maelstrom/feat/task-index',
      'northwind/feat/db-migrate',
      'northwind/feat/orders',
      'northwind/feat/reporting',
      'northwind/feat/rotate-auth-tokens-for-every-service',
    ]);
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

  it('one Search filters the Desk nodes and the Tasks rows', async () => {
    const user = userEvent.setup();
    await renderApp();
    const deskIds = () =>
      screen.getAllByTestId('task-node').map((node) => node.getAttribute('data-task-id'));

    await user.type(screen.getByLabelText('Search'), 'ORDER EXPORT');
    await waitFor(() => expect(deskIds().sort()).toEqual(['NORT-7', 'NORT-7.1']));

    await user.click(paneItem('Tasks'));
    expect(screen.getByLabelText('Search')).toHaveValue('ORDER EXPORT');
    const rowIds = [...screen.getByTestId('task-list').querySelectorAll('[data-task-id]')].map(
      (row) => row.getAttribute('data-task-id'),
    );
    expect(rowIds.sort()).toEqual(['NORT-7', 'NORT-7.1']);
  });

  it('shows the Search a Back returns to', async () => {
    const user = userEvent.setup();
    const { router } = await renderApp();
    const search = () => screen.getByLabelText('Search');
    await user.type(search(), 'order');
    await waitFor(() => expect(router.state.location.search).toBe('?q=order'));
    await user.selectOptions(screen.getByLabelText('Project'), 'northwind');
    await user.clear(search());
    await waitFor(() => expect(router.state.location.search).toBe('?project=northwind'));
    await act(() => router.navigate(-1));
    expect(search()).toHaveValue('order');
  });

  it('filtering by branch removes the nodes of other branches', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.selectOptions(screen.getByLabelText('Branch'), 'northwind/feat/orders');
    const nodes = [...document.querySelectorAll('[data-testid="task-node"]')];
    expect(nodes.map((n) => n.getAttribute('data-task-id')).sort()).toEqual(['NORT-7', 'NORT-7.1']);
  });

  it('labels the progress zones the board uses', async () => {
    await renderApp();
    const labels = [...document.querySelectorAll('[data-testid="zone-label"]')];
    // The desk holds no done task, so that zone collapses and draws no label.
    expect(labels.map((el) => el.textContent)).toEqual(['Running', 'Not started']);
  });

  // The canvas has one grouping: project lanes with worktree boxes.
  it('draws a lane per project', async () => {
    await renderApp();
    const lanes = [...document.querySelectorAll('[data-testid="group-node"]')];
    expect(lanes.map((l) => l.getAttribute('data-group-id'))).toEqual(['maelstrom', 'northwind']);
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

  it('a project lane draws a box for each worktree, empty ones included', async () => {
    const { server } = await renderApp();
    // Every box on the board, so one that should not draw cannot hide.
    const boxes = (empty: boolean) =>
      [...document.querySelectorAll('[data-testid="worktree-box"]')]
        .filter((box) => box.getAttribute('data-empty') === String(empty))
        .map((box) => box.getAttribute('data-worktree-id'));
    // Lane by lane, oldest node first: in northwind, NORT-12 in delta, then
    // NORT-9 in bravo, then NORT-7 in alpha. `_main` holds no node, so it has
    // no box. Every open worktree holds a node, so no empty box draws.
    expect(boxes(false)).toEqual([
      'maelstrom-bravo',
      'maelstrom-alpha',
      'northwind-delta',
      'northwind-bravo',
      'northwind-alpha',
    ]);
    expect(boxes(true)).toEqual([]);

    // Opening a worktree that holds nothing adds an empty box to its project.
    server.change({ kind: 'worktree', ids: ['northwind-charlie'] }, (w) => {
      w.worktrees['northwind-charlie']!.isClosed = false;
    });
    await waitFor(() => expect(boxes(true)).toEqual(['northwind-charlie']));
  });
});
