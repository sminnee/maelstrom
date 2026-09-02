import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { seedWorld } from './fake-backend/scenarios/seedWorld';
import { renderApp, stepSim } from './test/renderApp';

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
