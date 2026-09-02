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
    const user = (await import('@testing-library/user-event')).default.setup();
    const { backend } = await renderApp();
    const { stepSim } = await import('./test/renderApp');
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
    const user = (await import('@testing-library/user-event')).default.setup();
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
    const { fireEvent } = await import('@testing-library/react');
    const badge = document.querySelector('[data-task-id="NORT-7"] [aria-label="needs attention"]')!;
    fireEvent.click(badge);
    expect(screen.getByRole('tab', { selected: true })).toHaveAttribute(
      'data-tab-key',
      'document:doc-nort7-plan',
    );
  });
});
