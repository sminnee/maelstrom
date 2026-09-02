import { describe, expect, it } from 'vitest';
import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { seedWorld } from './fake-backend/scenarios/seedWorld';
import { clickNode, renderApp, stepSim } from './test/renderApp';

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
    expect(document.querySelector('[data-task-id="NORT-9"]')).toHaveAttribute(
      'data-state',
      'working',
    );
    backend.sim.force({ kind: 'ask', agentId: 'd9a4c7f1' });
    let changed = false;
    for (let i = 0; i < 6 && !changed; i += 1) {
      await stepSim(backend);
      changed =
        document.querySelector('[data-task-id="NORT-9"]')?.getAttribute('data-state') ===
        'needs-attention';
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

  it('grouping by branch shows one group per branch', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.selectOptions(screen.getByLabelText('Group by'), 'branch');
    const groups = [...document.querySelectorAll('[data-testid="group-node"]')];
    const branches = new Set(Object.values(seedWorld().world.tasks).map((t) => t.branch));
    expect(groups).toHaveLength(branches.size);
  });
});

describe('the panel', () => {
  it('clicking a node opens its summary tab once, showing what it waits on', async () => {
    await renderApp();
    clickNode('NORT-7');
    const panel = screen.getByTestId('panel');
    expect(panel).toHaveTextContent('Plan: order export');
    clickNode('NORT-7');
    expect(screen.getAllByRole('tab')).toHaveLength(1);
  });

  it('approving a plan from the summary tab clears the attention on the node and the chip', async () => {
    const user = userEvent.setup();
    await renderApp();
    const chip = screen.getByTestId('attention-chip');
    const before = Number(chip.textContent?.replace(/\D/g, ''));
    expect(clickNode('NORT-7')).toHaveAttribute('data-state', 'needs-attention');
    await user.click(screen.getByRole('button', { name: 'Approve' }));
    expect(document.querySelector('[data-task-id="NORT-7"]')).not.toHaveAttribute(
      'data-state',
      'needs-attention',
    );
    expect(Number(screen.getByTestId('attention-chip').textContent?.replace(/\D/g, ''))).toBe(
      before - 1,
    );
  });
});

describe('the attention chip', () => {
  it('opens the summary of the next node that needs the user, cycling on each click', async () => {
    const user = userEvent.setup();
    await renderApp();
    const chip = screen.getByTestId('attention-chip');
    await user.click(chip);
    const first = screen.getByRole('tab', { selected: true }).getAttribute('data-tab-key');
    await user.click(chip);
    const second = screen.getByRole('tab', { selected: true }).getAttribute('data-tab-key');
    expect(new Set([first, second])).toEqual(new Set(['summary:NORT-7', 'summary:MAEL-52']));
  });

  it('counts only the nodes the filters leave on the canvas', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.selectOptions(screen.getByLabelText('Project'), 'maelstrom');
    expect(screen.getByTestId('attention-chip')).toHaveAttribute('data-count', '1');
  });
});

describe('the session tab', () => {
  it('opens from the summary and sends a message the transcript then shows', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('NORT-9');
    await user.click(screen.getByRole('button', { name: 'Open session' }));
    expect(screen.getAllByRole('tab')).toHaveLength(2);
    const input = screen.getByRole('textbox', { name: 'Message to agent' });
    await user.type(input, 'Prefer the ICU collation.');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    expect(screen.getByText('Prefer the ICU collation.')).toBeInTheDocument();
  });
});

describe('document tabs', () => {
  it('two documents from two agents open as two attributed tabs that survive a third', async () => {
    const user = userEvent.setup();
    const { backend } = await renderApp();
    // A second plan, from a second agent, so there are two documents to open.
    backend.sim.force({ kind: 'plan', agentId: 'd9a4c7f1' });
    for (let i = 0; i < 8; i += 1) await stepSim(backend);

    clickNode('NORT-7');
    await user.click(screen.getByRole('button', { name: /plan\.md v1/ }));
    clickNode('NORT-9');
    await user.click(screen.getByRole('button', { name: /plan\.md v1/ }));
    const chips = () =>
      [...document.querySelectorAll('[role="tab"] [data-testid="tab-chip"]')].map(
        (c) => c.textContent,
      );
    const docTabs = () => [...document.querySelectorAll('[role="tab"][data-tab-key^="document:"]')];
    expect(docTabs()).toHaveLength(2);
    expect(
      new Set(docTabs().map((t) => t.querySelector('[data-testid="tab-chip"]')?.textContent)).size,
    ).toBe(2);
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Migrate to Postgres 16');

    clickNode('NORT-9');
    await user.click(screen.getByRole('button', { name: 'Open session' }));
    expect(docTabs()).toHaveLength(2);
    expect(chips().length).toBeGreaterThanOrEqual(3);
  });

  it('focusing a document tab focuses its node, and clicking another node moves the focus', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('NORT-7');
    await user.click(screen.getByRole('button', { name: /plan\.md v1/ }));
    expect(document.querySelector('[data-task-id="NORT-7"]')).toHaveAttribute('data-focused');
    clickNode('NORT-9');
    expect(screen.getByRole('tab', { selected: true })).toHaveAttribute(
      'data-tab-key',
      'summary:NORT-9',
    );
    expect(document.querySelector('[data-task-id="NORT-7"]')).not.toHaveAttribute('data-focused');
    expect(document.querySelector('[data-task-id="NORT-9"]')).toHaveAttribute('data-focused');
    await user.click(screen.getByRole('tab', { name: /plan\.md/ }));
    expect(document.querySelector('[data-task-id="NORT-7"]')).toHaveAttribute('data-focused');
  });

  it('the attention badge on a node opens its document', async () => {
    await renderApp();
    const badge = document.querySelector('[data-task-id="NORT-7"] [aria-label="needs attention"]')!;
    fireEvent.click(badge);
    expect(screen.getByRole('tab', { selected: true })).toHaveAttribute(
      'data-tab-key',
      'document:doc-nort7-plan',
    );
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
    await user.click(screen.getByRole('button', { name: 'Approve' }));
    backend.sim.force({ kind: 'ask', agentId: 'd9a4c7f1' });
    for (let i = 0; i < 6; i += 1) await stepSim(backend);
    expect(document.querySelector('[data-task-id="NORT-9"]')).toHaveAttribute(
      'data-state',
      'needs-attention',
    );

    clickNode('NORT-9');
    await user.click(screen.getByRole('button', { name: /plan\.md v1/ }));
    const tab = screen.getByTestId('document-tab');
    const inline = tab.querySelector('[data-testid="inline-question"]')!;
    expect(inline).not.toBeNull();
    await user.click(inline.querySelector('button')!);
    expect(document.querySelector('[data-task-id="NORT-9"]')).not.toHaveAttribute(
      'data-state',
      'needs-attention',
    );
  });

  it('a comment on selected text lands in the margin and request changes sends it to the agent', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('NORT-7');
    await user.click(screen.getByRole('button', { name: /plan\.md v1/ }));
    const body = screen.getByTestId('document-body');
    const text = [...body.querySelectorAll('li')].find((el) =>
      el.textContent?.includes('10,000 rows'),
    )!;
    const node = text.firstChild!;
    const range = document.createRange();
    range.setStart(node, 0);
    range.setEnd(node, 'Cap the export'.length);
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
    fireEvent.mouseUp(body);

    await user.type(screen.getByRole('textbox', { name: 'Comment' }), 'Make the cap configurable.');
    await user.click(screen.getByRole('button', { name: 'Add comment' }));
    const margin = screen.getByTestId('comment-margin');
    expect(margin).toHaveTextContent('Cap the export');
    expect(margin).toHaveTextContent('Make the cap configurable.');

    await user.click(screen.getByRole('button', { name: 'Request changes' }));
    expect(screen.getByTestId('document-tab')).toHaveTextContent('changes-requested');
    clickNode('NORT-7');
    await user.click(screen.getByRole('button', { name: 'Open session' }));
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Make the cap configurable.');
  });
});

describe('the debug drawer', () => {
  it('forcing a question makes the node need attention and raises the chip count', async () => {
    const user = userEvent.setup();
    await renderApp();
    const before = Number(screen.getByTestId('attention-chip').textContent?.replace(/\D/g, ''));
    await user.click(screen.getByRole('button', { name: 'Toggle debug drawer' }));
    const drawer = screen.getByTestId('debug-drawer');
    const row = within(drawer).getByTestId('drawer-agent-d9a4c7f1');
    await user.click(within(row).getByRole('button', { name: 'Ask' }));
    expect(document.querySelector('[data-task-id="NORT-9"]')).toHaveAttribute(
      'data-state',
      'needs-attention',
    );
    expect(Number(screen.getByTestId('attention-chip').textContent?.replace(/\D/g, ''))).toBe(
      before + 1,
    );
  });

  it('forcing an exit turns the node red', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.click(screen.getByRole('button', { name: 'Toggle debug drawer' }));
    const row = within(screen.getByTestId('debug-drawer')).getByTestId('drawer-agent-c3e8f1b5');
    await user.click(within(row).getByRole('button', { name: 'Exit 1' }));
    expect(document.querySelector('[data-task-id="MAEL-40.1"]')).toHaveAttribute(
      'data-state',
      'exited',
    );
    await user.click(screen.getByRole('button', { name: 'Toggle debug drawer' }));
    expect(screen.queryByTestId('debug-drawer')).toBeNull();
  });
});
