import { deskIdForTask } from './protocol/deskId';
import { describe, expect, it } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { act } from 'react';
import userEvent from '@testing-library/user-event';
import type { Attention } from './protocol/attention';
import type { Agent } from './protocol/entities';
import { TASK_STATUSES } from './protocol/entities';
import type { Document } from './protocol/documents';
import type { FakeServer } from './test/fakeServer';
import { clickNode, pressKey, renderApp, selectText } from './test/renderApp';
import { seedWorld } from './test/seedWorld';

/** The one expanded node, as the card it grew into. */
const expanded = () => screen.getByRole('dialog');
const chipCount = () =>
  Number(screen.getByTestId('attention-chip').textContent?.replace(/\D/g, ''));
const nodeState = (taskId: string) =>
  document.querySelector(`[data-task-id="${taskId}"]`)?.getAttribute('data-state');

/** Park NORT-9's agent on a question, as the server would after a control_request. */
function askQuestion(server: FakeServer) {
  const requestId = 'req-nort9-q';
  server.append('d9a4c7f1', {
    id: 'd9a4c7f1-q',
    ts: '',
    type: 'question',
    requestId,
    questions: [
      {
        question: 'Which columns?',
        header: 'Columns',
        multiSelect: true,
        options: [
          { label: 'Id', description: '' },
          { label: 'Total', description: '' },
        ],
      },
      {
        question: 'Stream or batch?',
        header: 'Export',
        multiSelect: false,
        options: [
          { label: 'Stream', description: '' },
          { label: 'Batch', description: '' },
        ],
      },
    ],
  });
  const attention: Attention = {
    id: 'att-nort9-q',
    kind: 'question',
    agentId: 'd9a4c7f1',
    taskId: 'NORT-9',
    documentId: null,
    requestId,
    summary: 'Which columns?',
    raisedAt: '2026-09-02T09:00:00.000Z',
    clearedAt: null,
  };
  server.change({ kind: 'agent', ids: ['d9a4c7f1'] }, (w) => {
    w.agents['d9a4c7f1'] = {
      ...w.agents['d9a4c7f1']!,
      state: 'awaiting-question',
      pendingRequestId: requestId,
      waitingOn: 'Which columns?',
    };
    w.attention[attention.id] = attention;
  });
  server.change({ kind: 'attention', ids: [attention.id] });
}

/** Give NORT-9 a plan document, as a plan review would. */
function addPlan(server: FakeServer, status: Document['status'] = 'approved') {
  const doc: Document = {
    id: 'doc-nort9-plan',
    agentId: 'd9a4c7f1',
    taskId: 'NORT-9',
    kind: 'plan',
    title: 'plan.md',
    markdown: '# Migrate to Postgres 16\n\nCarefully.\n',
    version: 1,
    status,
    source: { type: 'plan_review', requestId: 'req-nort9-plan', planFilePath: '' },
  };
  server.change({ kind: 'document', ids: [doc.id] }, (w) => {
    w.documents[doc.id] = doc;
  });
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
    expect(chipCount()).toBe(3);
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
});

describe('the expanded node', () => {
  it('clicking a node expands it in place with its state in words; a second click or Esc collapses it', async () => {
    await renderApp();
    clickNode('NORT-7');
    const card = screen.getByRole('dialog', { name: 'Plan the order export' });
    expect(card).toHaveTextContent('Needs you · plan review');
    expect(card).not.toHaveTextContent('awaiting-plan-review');
    expect(document.querySelector('[data-task-id="NORT-7"]')).toHaveAttribute('data-expanded');
    expect(screen.queryAllByRole('tab')).toHaveLength(0);
    clickNode('NORT-7');
    expect(screen.queryByRole('dialog')).toBeNull();
    clickNode('NORT-7');
    pressKey('Escape');
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('shows the task brief as markdown, collapsed', async () => {
    await renderApp();
    clickNode('NORT-7.1');
    // The list holds slim rows, so the card fetches the brief.
    const brief = await within(expanded()).findByTestId('task-content');
    // Markdown, not the raw source: the heading is a heading.
    expect(within(brief).getByRole('heading', { name: 'Seams under test' })).toBeInTheDocument();
    expect(brief).toHaveAttribute('data-expanded', 'false');
  });

  it('approving a plan from the expanded node clears the attention on the node and the chip', async () => {
    const user = userEvent.setup();
    await renderApp();
    const before = chipCount();
    expect(clickNode('NORT-7')).toHaveAttribute('data-state', 'needs-attention');
    // The decision fetches the agent's detail, so the prompt follows the card.
    await user.click(await within(expanded()).findByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(nodeState('NORT-7')).not.toBe('needs-attention'));
    expect(chipCount()).toBe(before - 1);
  });

  it('a refused approve shows Failed on the button and leaves the node needing attention', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    server.refuse(/POST \/api\/agents\/[^/]+\/approve$/, {
      status: 409,
      code: 'stale_request',
      message: 'Request is no longer pending',
    });
    expect(clickNode('NORT-7')).toHaveAttribute('data-state', 'needs-attention');
    await user.click(await within(expanded()).findByRole('button', { name: 'Approve' }));
    const failed = await within(expanded()).findByRole('button', { name: 'Failed' });
    expect(failed).toHaveAttribute('title', 'Request is no longer pending');
    expect(nodeState('NORT-7')).toBe('needs-attention');
  });

  it('shows the last messages before its question, then the prompt; Answer clears the attention', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('MAEL-52');
    const card = expanded();
    const prompt = await within(card).findByTestId('question-prompt');
    expect(card).toHaveTextContent('Before this');
    expect(card).toHaveTextContent('Two grouping defaults are plausible');
    await user.click(within(prompt).getAllByRole('radio')[0]!);
    await user.click(within(prompt).getByRole('button', { name: 'Answer' }));
    await waitFor(() => expect(nodeState('MAEL-52')).not.toBe('needs-attention'));
  });

  it("shows the task's notebook status", async () => {
    await renderApp();
    clickNode('NORT-9.1');
    expect(
      within(expanded()).getByRole('button', { name: 'Status of Watch the migration PR, todo' }),
    ).toBeInTheDocument();
  });

  it('moves the task when a status is picked, and the card follows', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('NORT-9.1');

    await user.click(
      within(expanded()).getByRole('button', { name: 'Status of Watch the migration PR, todo' }),
    );
    await user.selectOptions(within(expanded()).getByRole('combobox'), 'blocked');

    expect(
      await within(expanded()).findByRole('button', {
        name: 'Status of Watch the migration PR, blocked',
      }),
    ).toBeInTheDocument();
    expect(within(expanded()).queryByRole('combobox')).toBeNull();
  });

  it('closes the status picker on Escape and leaves the card open', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('NORT-9.1');

    await user.click(
      within(expanded()).getByRole('button', { name: 'Status of Watch the migration PR, todo' }),
    );
    await user.keyboard('{Escape}');

    expect(within(expanded()).queryByRole('combobox')).toBeNull();
    expect(
      within(expanded()).getByRole('button', { name: 'Status of Watch the migration PR, todo' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('shows a refused move in the card, and keeps the card open', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    server.refuse(/\/status$/, {
      status: 409,
      code: 'not_actionable',
      message: 'That task cannot move yet',
    });
    clickNode('NORT-9.1');

    await user.click(
      within(expanded()).getByRole('button', { name: 'Status of Watch the migration PR, todo' }),
    );
    await user.selectOptions(within(expanded()).getByRole('combobox'), 'blocked');

    expect(await within(expanded()).findByRole('alert')).toHaveTextContent(
      'That task cannot move yet',
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('says the state once when the derived words only restate the status', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('NORT-9.1');
    // Queued is a reading the status alone does not give, so both are shown.
    expect(within(expanded()).getByText('Queued')).toBeInTheDocument();

    await user.click(
      within(expanded()).getByRole('button', { name: 'Status of Watch the migration PR, todo' }),
    );
    await user.selectOptions(within(expanded()).getByRole('combobox'), 'blocked');

    // "Blocked" is just `blocked` in words, so the strip does not say it twice.
    expect(
      await within(expanded()).findByRole('button', {
        name: 'Status of Watch the migration PR, blocked',
      }),
    ).toBeInTheDocument();
    expect(within(expanded()).queryByText('Blocked')).toBeNull();
  });

  it('a free agent has no status to set, because it has no task', async () => {
    await renderApp();
    clickNode('f2c6a9d4');
    // The picker only appears once clicked, so its absence is the absence of
    // the button that opens it: no button on the card names a task status.
    const statuses = new Set<string>(TASK_STATUSES);
    const opener = within(expanded())
      .getAllByRole('button')
      .find((b) => statuses.has(b.textContent ?? ''));
    expect(opener).toBeUndefined();
  });
});

describe('the attention chip', () => {
  it('expands the next node that needs the user, cycling on each click', async () => {
    const user = userEvent.setup();
    await renderApp();
    const chip = screen.getByTestId('attention-chip');
    await user.click(chip);
    const first = expanded().getAttribute('aria-label');
    await user.click(chip);
    const second = expanded().getAttribute('aria-label');
    expect(new Set([first, second])).toEqual(
      new Set(['Plan the order export', 'Shape the orchestrator UI']),
    );
  });

  it('counts only the nodes the filters leave on the canvas', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.selectOptions(screen.getByLabelText('Project'), 'maelstrom');
    expect(screen.getByTestId('attention-chip')).toHaveAttribute('data-count', '1');
  });
});

describe('the session tab', () => {
  it('opens from the Session link in the expanded node and sends a message the transcript then shows', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('NORT-9');
    await user.click(within(expanded()).getByRole('link', { name: 'Session' }));
    expect(screen.getAllByRole('tab')).toHaveLength(1);
    const input = screen.getByRole('textbox', { name: 'Message to agent' });
    await user.type(input, 'Prefer the ICU collation.');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByText('Prefer the ICU collation.')).toBeInTheDocument();
    expect(input).toHaveValue('');
  });

  it('leaves one live prompt when the card and the session tab show the same wait', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('MAEL-52');
    await user.click(within(expanded()).getByRole('link', { name: 'Session' }));
    expect(screen.getAllByTestId('question-prompt')).toHaveLength(1);
    expect(within(expanded()).getByTestId('question-prompt')).toBeInTheDocument();
    expect(screen.getByTestId('deferred-wait')).toBeInTheDocument();
  });

  it('answers from the session tab when no card is expanded', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('MAEL-52');
    await user.click(within(expanded()).getByRole('link', { name: 'Session' }));
    await user.keyboard('{Escape}');
    const prompt = screen.getByTestId('question-prompt');
    await user.click(within(prompt).getAllByRole('radio')[0]!);
    await user.click(within(prompt).getByRole('button', { name: 'Answer' }));
    expect(nodeState('MAEL-52')).not.toBe('needs-attention');
  });

  it('lists the subagents under the transcript, and opens one as a read-only tab of its own', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    clickNode('NORT-9');
    await user.click(within(expanded()).getByRole('link', { name: 'Session' }));
    const strip = screen.getByTestId('subagent-strip');
    const link = within(strip).getByRole('link', { name: /Find every collation-sensitive query/ });
    expect(within(strip).getAllByRole('link')).toHaveLength(1);
    expect(link.querySelector('[data-state]')).toHaveAttribute('data-state', 'processing');
    // The parent's own transcript shows the call, folded, and none of the chatter under it.
    const parentPanel = screen.getByRole('tabpanel');
    expect(within(parentPanel).queryByText('Grep for ORDER BY name.')).not.toBeInTheDocument();

    await user.click(link);
    const keys = [...document.querySelectorAll('[role="tab"]')].map((t) =>
      t.getAttribute('data-tab-key'),
    );
    expect(keys).toEqual(['session:d9a4c7f1', 'session:d9a4c7f1.1']);
    const panel = screen.getByRole('tabpanel');
    await within(panel).findByText('Three queries order by name without a collation.');
    expect(within(panel).getByText('Grep for ORDER BY name.')).toBeInTheDocument();
    expect(panel).toHaveTextContent('d9a4c7f1.1 · Find every collation-sensitive query');
    expect(within(panel).queryByRole('textbox', { name: 'Message to agent' })).toBeNull();
    expect(within(panel).queryByRole('button', { name: 'normal' })).toBeNull();
    expect(within(panel).queryByTestId('subagent-strip')).toBeNull();
    expect(server.sockets.filter((s) => s.agentId === 'd9a4c7f1.1')).toHaveLength(1);
  });

  it('draws no subagent strip for an agent that has none', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('NORT-7');
    await user.click(within(expanded()).getByRole('link', { name: 'Session' }));
    expect(screen.queryByTestId('subagent-strip')).toBeNull();
  });

  it('cycles the permission mode from the chip in the head', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('NORT-9');
    await user.click(within(expanded()).getByRole('link', { name: 'Session' }));
    const chip = screen.getByRole('button', { name: 'normal' });
    await user.click(chip);
    expect(await screen.findByRole('button', { name: 'plan' })).toBeInTheDocument();
  });
});

describe('document tabs', () => {
  it('two documents from two expanded nodes open as two attributed tabs that survive a third', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    // A second plan, from a second agent, so there are two documents to open.
    addPlan(server);

    clickNode('NORT-7');
    await user.click(within(expanded()).getByRole('link', { name: /plan\.md v1/ }));
    clickNode('NORT-9');
    await user.click(await within(expanded()).findByRole('link', { name: /plan\.md v1/ }));
    const chips = () =>
      [...document.querySelectorAll('[role="tab"] [data-testid="tab-chip"]')].map(
        (c) => c.textContent,
      );
    const docTabs = () => [...document.querySelectorAll('[role="tab"][data-tab-key^="document:"]')];
    expect(
      docTabs()
        .map((t) => t.querySelector('[data-testid="tab-chip"]')?.textContent)
        .sort(),
    ).toEqual(['NORT-7', 'NORT-9']);
    await waitFor(() =>
      expect(screen.getByRole('tabpanel')).toHaveTextContent('Migrate to Postgres 16'),
    );

    // NORT-9 is still expanded: a third tab from the same card.
    await user.click(within(expanded()).getByRole('link', { name: 'Session' }));
    const keys = [...document.querySelectorAll('[role="tab"]')].map((t) =>
      t.getAttribute('data-tab-key'),
    );
    expect(keys).toHaveLength(3);
    expect(keys.filter((k) => k?.startsWith('document:'))).toHaveLength(2);
    expect(keys).toContain('session:d9a4c7f1');
    expect(chips()).toHaveLength(3);
  });

  it('the active tab focuses its node; expanding another node does not move the focus', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('NORT-7');
    await user.click(within(expanded()).getByRole('link', { name: /plan\.md v1/ }));
    expect(document.querySelector('[data-task-id="NORT-7"]')).toHaveAttribute('data-focused');
    clickNode('NORT-9');
    expect(document.querySelector('[data-task-id="NORT-9"]')).toHaveAttribute('data-expanded');
    expect(document.querySelector('[data-task-id="NORT-7"]')).toHaveAttribute('data-focused');
    await user.click(within(expanded()).getByRole('link', { name: 'Session' }));
    expect(document.querySelector('[data-task-id="NORT-7"]')).not.toHaveAttribute('data-focused');
    expect(document.querySelector('[data-task-id="NORT-9"]')).toHaveAttribute('data-focused');
    await user.click(screen.getByRole('tab', { name: /plan\.md/ }));
    expect(document.querySelector('[data-task-id="NORT-7"]')).toHaveAttribute('data-focused');
  });

  it('the attention badge opens the document behind it, or expands the node when there is none', async () => {
    await renderApp();
    const badge = (taskId: string) =>
      document.querySelector(`[data-task-id="${taskId}"] [aria-label^="needs attention"]`)!;
    fireEvent.click(badge('NORT-7'));
    expect(screen.getByRole('tab', { selected: true })).toHaveAttribute(
      'data-tab-key',
      'document:doc-nort7-plan',
    );
    fireEvent.click(badge('MAEL-52'));
    expect(screen.getByRole('dialog', { name: 'Shape the orchestrator UI' })).toBeInTheDocument();
  });
});

describe('review in a document tab', () => {
  it('answers a question inline and the node leaves needs-attention', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    // NORT-9's agent has an approved plan, and now asks a question.
    addPlan(server);
    askQuestion(server);
    await waitFor(() => expect(nodeState('NORT-9')).toBe('needs-attention'));
    clickNode('NORT-9');

    await user.click(await within(expanded()).findByRole('link', { name: /plan\.md v1/ }));
    const tab = await screen.findByTestId('document-tab');
    const inline = within(await within(tab).findByTestId('inline-decision'));
    // The decision shows what the agent said before it asked.
    expect(await inline.findByText('Before this')).toBeInTheDocument();
    await user.click(inline.getAllByRole('checkbox')[0]!);
    await user.click(inline.getByRole('button', { name: 'Next' }));
    await user.click(inline.getAllByRole('radio')[0]!);
    await user.click(inline.getByRole('button', { name: 'Answer' }));
    await waitFor(() => expect(nodeState('NORT-9')).not.toBe('needs-attention'));
  });

  it('one drag offers a comment; adding it and requesting changes say the server does not do that yet', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('NORT-7');
    await user.click(within(expanded()).getByRole('link', { name: /plan\.md v1/ }));
    const body = await screen.findByTestId('document-body');
    const text = [...body.querySelectorAll('li')].find((el) =>
      el.textContent?.includes('10,000 rows'),
    )!;
    selectText(text.firstChild!, 0, 'Cap the export'.length);
    // The control appears at once; no second click on the text is needed.
    expect(screen.queryByRole('textbox', { name: 'Comment' })).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Comment on selection' }));
    expect(screen.getByTestId('comment-margin')).toHaveTextContent('Cap the export');
    await user.type(screen.getByRole('textbox', { name: 'Comment' }), 'Make the cap configurable.');
    await user.click(screen.getByRole('button', { name: 'Add comment' }));
    // The server answers 501: the button says so, and the draft stays for a retry.
    const add = await screen.findByRole('button', { name: 'Not implemented yet' });
    expect(add).toHaveAttribute('title', expect.stringContaining('not implemented'));
    expect(screen.getByRole('textbox', { name: 'Comment' })).toHaveValue(
      'Make the cap configurable.',
    );

    await user.type(
      screen.getByRole('textbox', { name: 'Summary of requested changes' }),
      'Tighten it.',
    );
    await user.click(screen.getByRole('button', { name: 'Request changes' }));
    expect(await screen.findAllByRole('button', { name: 'Not implemented yet' })).toHaveLength(2);
    expect(screen.getByTestId('document-tab')).toHaveTextContent('awaiting review');
  });
});

describe('the task list', () => {
  const goToList = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(screen.getByRole('button', { name: 'Task list' }));
    return screen.getByTestId('task-list');
  };
  const listRow = (taskId: string) =>
    document.querySelector(`[data-testid="task-list"] [data-task-id="${taskId}"]`)!;
  const listedIds = () =>
    within(screen.getByTestId('task-list'))
      .getAllByRole('row')
      .map((r) => r.getAttribute('data-task-id'))
      .filter(Boolean)
      .sort();
  /** Tick every status back on, so finished tasks are listed too. */
  const showEveryStatus = async (user: ReturnType<typeof userEvent.setup>) => {
    for (const status of ['done', 'cancelled', 'template']) {
      await user.click(screen.getByRole('checkbox', { name: status }));
    }
  };

  it('opens on live work, and ticking the rest lists every task in the world', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToList(user);
    const tasks = Object.values(seedWorld().world.tasks);
    const live = tasks.filter((t) => ['todo', 'in-progress', 'blocked'].includes(t.status));
    expect(live.length).toBeLessThan(tasks.length);
    expect(listedIds()).toEqual(live.map((t) => t.id).sort());

    await showEveryStatus(user);
    expect(listedIds()).toEqual(tasks.map((t) => t.id).sort());
  });

  it('adds a task to the desk, and it is then drawn on the canvas', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToList(user);
    await showEveryStatus(user);
    expect(listRow('NORT-3')).toHaveAttribute('data-on-desk', 'false');

    await user.click(
      within(listRow('NORT-3') as HTMLElement).getByRole('button', { name: 'Add to desk' }),
    );
    await waitFor(() => expect(listRow('NORT-3')).toHaveAttribute('data-on-desk', 'true'));

    await user.click(screen.getByRole('button', { name: 'Canvas' }));
    expect(document.querySelector('[data-task-id="NORT-3"]')).toBeInTheDocument();
  });

  it('removes a task with no agent from the desk, and it leaves the canvas', async () => {
    const user = userEvent.setup();
    await renderApp();
    expect(document.querySelector('[data-task-id="NORT-9.1"]')).toBeInTheDocument();

    await goToList(user);
    await user.click(
      within(listRow('NORT-9.1') as HTMLElement).getByRole('button', { name: 'Remove from desk' }),
    );
    await waitFor(() => expect(listRow('NORT-9.1')).toHaveAttribute('data-on-desk', 'false'));

    await user.click(screen.getByRole('button', { name: 'Canvas' }));
    expect(document.querySelector('[data-task-id="NORT-9.1"]')).not.toBeInTheDocument();
  });

  it('a task removed while its agent runs stays on the canvas until it stops', async () => {
    const user = userEvent.setup();
    await renderApp();

    await goToList(user);
    await user.click(
      within(listRow('NORT-9') as HTMLElement).getByRole('button', { name: 'Remove from desk' }),
    );
    await waitFor(() => expect(listRow('NORT-9')).toHaveAttribute('data-on-desk', 'false'));

    await user.click(screen.getByRole('button', { name: 'Canvas' }));
    expect(document.querySelector('[data-task-id="NORT-9"]')).toBeInTheDocument();
  });

  it('shows a status as text until it is clicked, then moves the task', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToList(user);
    const row = () => listRow('NORT-9') as HTMLElement;
    expect(within(row()).queryByRole('combobox')).toBeNull();

    await user.click(within(row()).getByRole('button', { name: 'in-progress' }));
    await user.selectOptions(within(row()).getByRole('combobox'), 'blocked');

    expect(await within(row()).findByRole('button', { name: 'blocked' })).toBeInTheDocument();
    expect(within(row()).queryByRole('combobox')).toBeNull();
  });

  it('a task moved to done leaves the list, and the done filter brings it back', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToList(user);

    await user.click(
      within(listRow('NORT-9') as HTMLElement).getByRole('button', { name: 'in-progress' }),
    );
    await user.selectOptions(
      within(listRow('NORT-9') as HTMLElement).getByRole('combobox'),
      'done',
    );

    // The default filter hides done work, so the row goes. That is the filter
    // doing its job, not the move failing.
    await waitFor(() => expect(listRow('NORT-9')).toBeNull());
    await user.click(screen.getByRole('checkbox', { name: 'done' }));
    expect(listRow('NORT-9')).not.toBeNull();
    expect(
      within(listRow('NORT-9') as HTMLElement).getByRole('button', { name: 'done' }),
    ).toBeInTheDocument();
  });

  it('closes the status picker on Escape without moving the task', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToList(user);
    const row = () => listRow('NORT-9') as HTMLElement;

    await user.click(within(row()).getByRole('button', { name: 'in-progress' }));
    await user.keyboard('{Escape}');

    expect(within(row()).queryByRole('combobox')).toBeNull();
    expect(within(row()).getByRole('button', { name: 'in-progress' })).toBeInTheDocument();
  });

  it('opens the editor seeded from the task, and saving writes the new title', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToList(user);
    await user.click(
      within(listRow('NORT-9') as HTMLElement).getByRole('button', { name: 'Edit' }),
    );

    // The editor fetches the task's prose, so the form follows the click.
    const editor = await screen.findByRole('dialog', { name: 'Migrate to Postgres 16' });
    const title = within(editor).getByLabelText('Title');
    expect(title).toHaveValue('Migrate to Postgres 16');
    await user.clear(title);
    await user.type(title, 'Migrate to Postgres 17');
    await user.click(within(editor).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(listRow('NORT-9')).toHaveTextContent('Migrate to Postgres 17'));
  });

  it('leaves a task that names no model inheriting the default', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    await goToList(user);
    await user.click(
      within(listRow('NORT-9') as HTMLElement).getByRole('button', { name: 'Edit' }),
    );

    // `docs/guide/planning.md` asks for an unset model on execute drafts, so
    // opening one must not pin it. Saving an unrelated field sends no model.
    const editor = await screen.findByRole('dialog', { name: 'Migrate to Postgres 16' });
    await user.click(within(editor).getByText('Advanced'));
    expect(within(editor).getByLabelText('Model')).toHaveValue('');
    const title = within(editor).getByLabelText('Title');
    await user.clear(title);
    await user.type(title, 'Migrate to Postgres 17');
    await user.click(within(editor).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    const patch = server.requests.filter((r) => r.method === 'PATCH').at(-1);
    expect(patch!.body).not.toHaveProperty('model');
  });

  it('can put a task back on the inherited default', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    server.world.tasks['NORT-9']!.model = 'opus';
    await goToList(user);
    await user.click(
      within(listRow('NORT-9') as HTMLElement).getByRole('button', { name: 'Edit' }),
    );

    const editor = await screen.findByRole('dialog', { name: 'Migrate to Postgres 16' });
    await user.click(within(editor).getByText('Advanced'));
    await user.selectOptions(within(editor).getByLabelText('Model'), '');
    await user.click(within(editor).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    const patch = server.requests.filter((r) => r.method === 'PATCH').at(-1);
    expect(patch!.body).toMatchObject({ model: '' });
  });

  it('keeps a stored model the shortlist does not name', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    // The editor fetches the task itself, so seeding the stored model here
    // reaches it: this is a value written before the shortlist existed.
    server.world.tasks['NORT-9']!.model = 'claude-opus-4-1-20250805';
    await goToList(user);
    await user.click(
      within(listRow('NORT-9') as HTMLElement).getByRole('button', { name: 'Edit' }),
    );

    // The notebook's model field is free-form, so a value this build does not
    // list is offered rather than dropped — otherwise opening the task would
    // quietly rewrite it.
    const editor = await screen.findByRole('dialog', { name: 'Migrate to Postgres 16' });
    await user.click(within(editor).getByText('Advanced'));
    expect(within(editor).getByLabelText('Model')).toHaveValue('claude-opus-4-1-20250805');
  });

  it('keeps the advanced fields folded away until they are asked for', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToList(user);
    await user.click(
      within(listRow('NORT-9') as HTMLElement).getByRole('button', { name: 'Edit' }),
    );

    const editor = await screen.findByRole('dialog', { name: 'Migrate to Postgres 16' });
    expect(within(editor).queryByLabelText('Command')).not.toBeVisible();
    await user.click(within(editor).getByText('Advanced'));
    expect(within(editor).getByLabelText('Command')).toBeVisible();
  });

  it('sends the fields the user changed, not those the world changed under them', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    await goToList(user);
    await user.click(
      within(listRow('NORT-9') as HTMLElement).getByRole('button', { name: 'Edit' }),
    );

    const editor = await screen.findByRole('dialog', { name: 'Migrate to Postgres 16' });
    const title = within(editor).getByLabelText('Title');
    await user.clear(title);
    await user.type(title, 'Migrate to Postgres 17');
    // The world moves while the editor is open: the branch changes elsewhere.
    server.change({ kind: 'task', ids: ['NORT-9'] }, (w) => {
      w.tasks['NORT-9'] = { ...w.tasks['NORT-9']!, branch: 'feat/db-migrate-2' };
    });
    await waitFor(() => expect(listRow('NORT-9')).toHaveTextContent('feat/db-migrate-2'));
    await user.click(within(editor).getByRole('button', { name: 'Save' }));

    // The title the user typed lands; the branch they never touched is not
    // overwritten with the value the editor opened on.
    await waitFor(() => expect(listRow('NORT-9')).toHaveTextContent('Migrate to Postgres 17'));
    expect(listRow('NORT-9')).toHaveTextContent('feat/db-migrate-2');
    const patch = server.requests.find((r) => r.method === 'PATCH');
    expect(patch?.body).toEqual({ title: 'Migrate to Postgres 17' });
  });

  it('closes the editor on Escape when nothing was typed', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToList(user);
    await user.click(
      within(listRow('NORT-9') as HTMLElement).getByRole('button', { name: 'Edit' }),
    );
    await screen.findByRole('dialog', { name: 'Migrate to Postgres 16' });

    await user.keyboard('{Escape}');

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(listRow('NORT-9')).toHaveTextContent('Migrate to Postgres 16');
  });

  it('asks before it throws away typed edits, and keeps them if you say no', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToList(user);
    await user.click(
      within(listRow('NORT-9') as HTMLElement).getByRole('button', { name: 'Edit' }),
    );

    const title = within(
      await screen.findByRole('dialog', { name: 'Migrate to Postgres 16' }),
    ).getByLabelText('Title');
    await user.clear(title);
    await user.type(title, 'Never saved');
    await user.keyboard('{Escape}');

    // The editor stays, holding what was typed, until the discard is confirmed.
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(within(screen.getByRole('dialog')).getByLabelText('Title')).toHaveValue('Never saved');
    await user.click(screen.getByRole('button', { name: 'Keep editing' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('button', { name: 'Discard' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(listRow('NORT-9')).toHaveTextContent('Migrate to Postgres 16');
  });

  it('the attention chip still counts an agent blocked on an off-desk task', async () => {
    const user = userEvent.setup();
    await renderApp();
    const before = chipCount();
    expect(before).toBeGreaterThan(0);

    // Clear the desk, so the chip is counted against off-desk work.
    await goToList(user);
    for (const r of Array.from(
      document.querySelectorAll('[data-testid="task-list"] [data-on-desk="true"]'),
    )) {
      await user.click(within(r as HTMLElement).getByRole('button', { name: 'Remove from desk' }));
    }
    await waitFor(() => expect(document.querySelectorAll('[data-on-desk="true"]')).toHaveLength(0));
    expect(chipCount()).toBe(before);

    // Following the chip puts its task back on the desk so it has a node.
    await user.click(screen.getByTestId('attention-chip'));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('the attention chip returns to the canvas and expands the node', async () => {
    const user = userEvent.setup();
    await renderApp();
    await goToList(user);
    expect(screen.queryByTestId('task-node')).not.toBeInTheDocument();

    await user.click(screen.getByTestId('attention-chip'));
    expect(screen.getByRole('button', { name: 'Canvas' })).toHaveAttribute('aria-pressed', 'true');
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });
});

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

describe('the transcript stream', () => {
  /**
   * Waits for the first item to arrive over a freshly-opened transcript socket.
   * A cold CI runner takes far longer over this than the default 1 s budget:
   * the tab mounts, opens a socket and renders "Loading the transcript…" until
   * the first frame lands.
   */
  const findFirstTranscriptItem = (panel: HTMLElement) =>
    within(panel).findByText('Rewriting the migration for the new collation.', undefined, {
      timeout: 10_000,
    });

  it('a session tab keeps its items across a socket drop and takes what it missed once', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    clickNode('NORT-9');
    await user.click(within(expanded()).getByRole('link', { name: 'Session' }));
    const panel = screen.getByRole('tabpanel');
    await findFirstTranscriptItem(panel);
    const before = within(panel).getAllByTestId('transcript-card').length;

    await act(async () => {
      server.dropSockets('d9a4c7f1');
    });
    // The items stay on screen while the stream is down.
    expect(within(panel).getAllByTestId('transcript-card')).toHaveLength(before);
    // Missed while down: the reconnect replays it from the cursor, once.
    server.append('d9a4c7f1', {
      id: 'x1',
      ts: '',
      type: 'message',
      role: 'assistant',
      markdown: 'Back again.',
    });
    await within(panel).findByText('Back again.');
    expect(within(panel).getAllByTestId('transcript-card')).toHaveLength(before + 1);
    expect(server.sockets.filter((s) => s.agentId === 'd9a4c7f1')).toHaveLength(2);
  });

  it('opens the transcript socket under StrictMode, whose remount reuses the streams', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp({ strict: true });
    clickNode('NORT-9');
    await user.click(within(expanded()).getByRole('link', { name: 'Session' }));
    const panel = screen.getByRole('tabpanel');
    await findFirstTranscriptItem(panel);
    expect(server.sockets.filter((s) => s.agentId === 'd9a4c7f1')).toHaveLength(1);
  });
});

describe('new work', () => {
  /** Open the form from the top bar and return its dialog. */
  async function openNewWork(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole('button', { name: 'New' }));
    return screen.getByRole('dialog', { name: 'New work' });
  }

  /**
   * The agent this run started, not one the seeded world already held.
   * The fake host mints a started agent's id with a `new` prefix, and the
   * seed has free agents of its own — so "the agent with no task" would
   * find one of those and pass whatever the form sent.
   */
  function startedAgent(server: FakeServer): Agent {
    const started = Object.values(server.world.agents).filter((a) => a.id.startsWith('new'));
    expect(started).toHaveLength(1);
    return started[0]!;
  }

  it('is reachable from the top bar in both views', async () => {
    const user = userEvent.setup();
    await renderApp();
    expect(screen.getByRole('button', { name: 'New' })).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Task list' }));
    expect(screen.getByRole('button', { name: 'New' })).toBeVisible();
  });

  it('holds Next back until the draft has something in it', async () => {
    const user = userEvent.setup();
    await renderApp();
    const form = await openNewWork(user);
    expect(within(form).getByRole('button', { name: 'Next' })).toBeDisabled();
    await user.type(within(form).getByLabelText('What needs doing?'), 'The export drops a row');
    expect(within(form).getByRole('button', { name: 'Next' })).toBeEnabled();
  });

  it('names the task from the draft, then saves it as todo onto the desk', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const form = await openNewWork(user);
    await user.selectOptions(within(form).getByLabelText('Project'), 'northwind');
    await user.type(within(form).getByLabelText('What needs doing?'), 'The export drops a row');
    await user.click(within(form).getByRole('button', { name: 'Next' }));

    // Step 2 arrives with the fields the user never typed, filled in.
    const title = await within(form).findByLabelText('Title');
    expect(title).toHaveValue('The export drops a row');
    // The value comes from the server, so the test pins that a branch was
    // filled in without the user typing one — not the fake's own slug.
    expect((within(form).getByLabelText('Branch') as HTMLInputElement).value).toMatch(/^feat\/.+/);
    // The prose becomes the content verbatim; inference names it, never rewrites it.
    expect(within(form).getByLabelText('Content')).toHaveValue('The export drops a row');

    // Every inferred field stays editable.
    await user.clear(title);
    await user.type(title, 'Fix the export');
    await user.click(within(form).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());
    const created = Object.values(server.world.tasks).find((t) => t.title === 'Fix the export');
    expect(created).toBeDefined();
    expect(created!.status).toBe('todo');
    // Saved work joins the desk, so what was just ordered is on the canvas.
    expect(server.world.desk[`task:${created!.id}`]).toBeDefined();
  });

  it('starts the task it creates when Start is pressed instead', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const form = await openNewWork(user);
    await user.type(within(form).getByLabelText('What needs doing?'), 'The export drops a row');
    await user.click(within(form).getByRole('button', { name: 'Next' }));
    await within(form).findByLabelText('Title');
    await user.click(within(form).getByRole('button', { name: 'Start' }));

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());
    const created = Object.values(server.world.tasks).find(
      (t) => t.title === 'The export drops a row',
    );
    expect(created!.status).toBe('in-progress');
    expect(Object.values(server.world.agents).some((a) => a.taskId === created!.id)).toBe(true);
  });

  it('starts a free agent on a branch, writing no task at all', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const before = Object.keys(server.world.tasks).length;
    const form = await openNewWork(user);
    await user.selectOptions(within(form).getByLabelText('Project'), 'northwind');
    await user.click(within(form).getByRole('radio', { name: 'Free agent' }));
    await user.type(within(form).getByLabelText('Branch'), 'feat/orders');
    await user.type(within(form).getByLabelText('What needs doing?'), 'Read the logs');
    await user.click(within(form).getByRole('button', { name: 'Start' }));

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());
    // No task was written: a free agent is work with no notebook entry.
    expect(Object.keys(server.world.tasks)).toHaveLength(before);
    const free = startedAgent(server);
    expect(free.taskId).toBe('');
    expect(server.world.desk[`agent:${free.id}`]).toBeDefined();
    // Unchosen, a free agent runs the same defaults a new task does.
    expect(free.permissionMode).toBe('plan');
    expect(free.model).toBe('opus');
  });

  it('starts a free agent under the mode and model the form chose', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const form = await openNewWork(user);
    await user.selectOptions(within(form).getByLabelText('Project'), 'northwind');
    await user.click(within(form).getByRole('radio', { name: 'Free agent' }));
    await user.type(within(form).getByLabelText('Branch'), 'feat/orders');
    await user.type(within(form).getByLabelText('What needs doing?'), 'Read the logs');
    await user.selectOptions(within(form).getByLabelText('Mode'), 'auto');
    await user.selectOptions(within(form).getByLabelText('Model'), 'fable');
    await user.click(within(form).getByRole('button', { name: 'Start' }));

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New work' })).toBeNull());
    const free = startedAgent(server);
    expect(free.permissionMode).toBe('auto');
    expect(free.model).toBe('fable');
  });

  it('never offers to write the task twice when only its launch failed', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const form = await openNewWork(user);
    await user.type(within(form).getByLabelText('What needs doing?'), 'The export drops a row');
    await user.click(within(form).getByRole('button', { name: 'Next' }));
    await within(form).findByLabelText('Title');
    // The task is written; the launch that follows it is refused.
    server.refuse(/api\/tasks$/, {
      status: 409,
      code: 'agent_exited',
      message: 'Agent has exited',
      taskId: 'northwind/NEW-1',
    });
    await user.click(within(form).getByRole('button', { name: 'Start' }));

    // The form says the task survived, and stops offering to write it again.
    expect(await within(form).findByTestId('new-work-error')).toHaveTextContent('northwind/NEW-1');
    expect(within(form).getByRole('button', { name: 'Save' })).toBeDisabled();
    const creates = server.requests.filter((r) => r.method === 'POST' && r.path === '/api/tasks');
    expect(creates).toHaveLength(1);
  });

  it('shows a refused start rather than closing on it', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    server.refuse(/api\/agents$/, { status: 400, code: 'invalid', message: 'No such branch' });
    const form = await openNewWork(user);
    await user.click(within(form).getByRole('radio', { name: 'Free agent' }));
    await user.type(within(form).getByLabelText('Branch'), 'feat/nope');
    await user.type(within(form).getByLabelText('What needs doing?'), 'Read the logs');
    await user.click(within(form).getByRole('button', { name: 'Start' }));

    expect(await within(form).findByTestId('new-work-error')).toHaveTextContent('No such branch');
    // The form stays, holding what was typed.
    expect(screen.getByRole('dialog', { name: 'New work' })).toBeVisible();
  });
});
