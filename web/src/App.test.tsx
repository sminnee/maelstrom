import { describe, expect, it } from 'vitest';
import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { seedWorld } from './fake-backend/scenarios/seedWorld';
import { clickNode, pressKey, renderApp, selectText, stepSim } from './test/renderApp';

/** The one expanded node, as the card it grew into. */
const expanded = () => screen.getByRole('dialog');
const chipCount = () =>
  Number(screen.getByTestId('attention-chip').textContent?.replace(/\D/g, ''));
const nodeState = (taskId: string) =>
  document.querySelector(`[data-task-id="${taskId}"]`)?.getAttribute('data-state');

describe('App', () => {
  it('renders the app title', async () => {
    await renderApp();
    expect(screen.getByRole('heading', { name: 'maelstrom' })).toBeInTheDocument();
  });

  it('renders one node per task in the seed world', async () => {
    await renderApp();
    const expected = Object.values(seedWorld().world.tasks)
      .filter((t) => t.status !== 'template')
      .map((t) => t.id)
      .sort();
    const rendered = (await screen.findAllByTestId('task-node'))
      .map((n) => n.getAttribute('data-task-id'))
      .sort();
    expect(rendered).toEqual(expected);
  });
});

describe('the simulation on the canvas', () => {
  it('a stepped event moves a working node into needs-attention', async () => {
    const { backend } = await renderApp();
    expect(nodeState('NORT-9')).toBe('working');
    backend.sim.force({ kind: 'ask', agentId: 'd9a4c7f1' });
    let changed = false;
    for (let i = 0; i < 6 && !changed; i += 1) {
      await stepSim(backend);
      changed = nodeState('NORT-9') === 'needs-attention';
    }
    expect(changed).toBe(true);
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
    const branches = new Set(Object.values(seedWorld().world.tasks).map((t) => t.branch));
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

  it('approving a plan from the expanded node clears the attention on the node and the chip', async () => {
    const user = userEvent.setup();
    await renderApp();
    const before = chipCount();
    expect(clickNode('NORT-7')).toHaveAttribute('data-state', 'needs-attention');
    await user.click(within(expanded()).getByRole('button', { name: 'Approve' }));
    expect(nodeState('NORT-7')).not.toBe('needs-attention');
    expect(chipCount()).toBe(before - 1);
  });

  it('shows the last messages before its question, then the prompt; Answer clears the attention', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('MAEL-52');
    const card = expanded();
    expect(card).toHaveTextContent('Before this');
    expect(card).toHaveTextContent('Two grouping defaults are plausible');
    const prompt = within(card).getByTestId('question-prompt');
    await user.click(within(prompt).getAllByRole('radio')[0]!);
    await user.click(within(prompt).getByRole('button', { name: 'Answer' }));
    expect(nodeState('MAEL-52')).not.toBe('needs-attention');
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
    expect(screen.getByText('Prefer the ICU collation.')).toBeInTheDocument();
  });
});

describe('document tabs', () => {
  it('two documents from two expanded nodes open as two attributed tabs that survive a third', async () => {
    const user = userEvent.setup();
    const { backend } = await renderApp();
    // A second plan, from a second agent, so there are two documents to open.
    backend.sim.force({ kind: 'plan', agentId: 'd9a4c7f1' });
    for (let i = 0; i < 8; i += 1) await stepSim(backend);

    clickNode('NORT-7');
    await user.click(within(expanded()).getByRole('link', { name: /plan\.md v1/ }));
    clickNode('NORT-9');
    await user.click(within(expanded()).getByRole('link', { name: /plan\.md v1/ }));
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
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Migrate to Postgres 16');

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
    const { backend } = await renderApp();
    // NORT-9's agent produces a plan, gets it approved, then asks a question.
    backend.sim.force({ kind: 'plan', agentId: 'd9a4c7f1' });
    for (let i = 0; i < 8; i += 1) await stepSim(backend);
    clickNode('NORT-9');
    await user.click(within(expanded()).getByRole('button', { name: 'Approve' }));
    backend.sim.force({ kind: 'ask', agentId: 'd9a4c7f1' });
    for (let i = 0; i < 6; i += 1) await stepSim(backend);
    expect(nodeState('NORT-9')).toBe('needs-attention');

    await user.click(within(expanded()).getByRole('link', { name: /plan\.md v1/ }));
    const tab = screen.getByTestId('document-tab');
    const inline = within(tab.querySelector('[data-testid="inline-decision"]') as HTMLElement);
    // The decision shows what the agent said before it asked.
    expect(inline.getByText('Before this')).toBeInTheDocument();
    await user.click(inline.getAllByRole('checkbox')[0]!);
    await user.click(inline.getByRole('button', { name: 'Next' }));
    await user.click(inline.getAllByRole('radio')[0]!);
    await user.click(inline.getByRole('button', { name: 'Answer' }));
    expect(nodeState('NORT-9')).not.toBe('needs-attention');
  });

  it('one drag offers a comment; the composer opens on click, and request changes sends it to the agent', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('NORT-7');
    await user.click(within(expanded()).getByRole('link', { name: /plan\.md v1/ }));
    const body = screen.getByTestId('document-body');
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
    const margin = screen.getByTestId('comment-margin');
    expect(margin).toHaveTextContent('Cap the export');
    expect(margin).toHaveTextContent('Make the cap configurable.');

    await user.click(screen.getByRole('button', { name: 'Request changes' }));
    expect(screen.getByTestId('document-tab')).toHaveTextContent('changes-requested');
    await user.click(within(expanded()).getByRole('link', { name: 'Session' }));
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Make the cap configurable.');
  }, 15_000);
});

describe('the debug drawer', () => {
  it('forcing a question makes the node need attention and raises the chip count', async () => {
    const user = userEvent.setup();
    await renderApp();
    // The seed world opens with two items: NORT-7's plan review and MAEL-52's question.
    expect(chipCount()).toBe(2);
    await user.click(screen.getByRole('button', { name: 'Toggle debug drawer' }));
    const drawer = screen.getByTestId('debug-drawer');
    const row = within(drawer).getByTestId('drawer-agent-d9a4c7f1');
    await user.click(within(row).getByRole('button', { name: 'Ask' }));
    expect(nodeState('NORT-9')).toBe('needs-attention');
    expect(chipCount()).toBe(3);
  });

  it('forcing an exit turns the node red', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.click(screen.getByRole('button', { name: 'Toggle debug drawer' }));
    const row = within(screen.getByTestId('debug-drawer')).getByTestId('drawer-agent-c3e8f1b5');
    await user.click(within(row).getByRole('button', { name: 'Exit 1' }));
    expect(nodeState('MAEL-40.1')).toBe('exited');
  });

  it('the toggle closes the drawer again', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.click(screen.getByRole('button', { name: 'Toggle debug drawer' }));
    expect(screen.getByTestId('debug-drawer')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Toggle debug drawer' }));
    expect(screen.queryByTestId('debug-drawer')).toBeNull();
  });
});
