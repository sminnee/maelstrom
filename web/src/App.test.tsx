import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
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
