import { describe, expect, it } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { act } from 'react';
import userEvent from '@testing-library/user-event';
import { expanded } from './test/appHelpers';
import { renderApp } from './test/renderApp';

describe('the usage and agent chips', () => {
  it('shows no usage chip until the host has a reading', async () => {
    await renderApp();
    // Anchor on a chip the same render does produce, or the two negatives
    // below would pass equally on a bar that has not painted yet.
    await screen.findByLabelText(/agents working/);
    expect(screen.queryByLabelText(/5-hour limit/)).toBeNull();
    expect(screen.queryByLabelText(/7-day limit/)).toBeNull();
  });

  it('reads both windows once the host reports them', async () => {
    const { server } = await renderApp();
    await act(async () => {
      server.change({ kind: 'host', ids: ['agent-host'] }, (world) => {
        world.host = {
          ...world.host!,
          usage: {
            fiveHour: { utilization: 0.07, resetsAt: Math.floor(Date.now() / 1000) + 3600 },
            sevenDay: { utilization: 0.24, resetsAt: Math.floor(Date.now() / 1000) + 86_400 },
            at: new Date().toISOString(),
          },
        };
      });
    });
    await waitFor(() => expect(screen.getByLabelText(/5-hour limit: 7% used/)).toBeInTheDocument());
    expect(screen.getByLabelText(/7-day limit: 24% used/)).toBeInTheDocument();
  });

  it('counts the agents that are working over those that are open', async () => {
    await renderApp();
    // The seed is deterministic: six top-level agents, four of them mid-turn.
    // The literal is what makes this catch a miscount -- a regex over the
    // shape would pass on "0 of 0" and on any wrong arithmetic.
    expect(await screen.findByLabelText('4 of 6 agents working, 2 idle')).toBeInTheDocument();
  });
});

describe('the attention chip', () => {
  it('expands the next node that needs the user, cycling on each click', async () => {
    const user = userEvent.setup();
    await renderApp();
    const chip = screen.getByTestId('attention-chip');
    const seen: (string | null)[] = [];
    for (let i = 0; i < 3; i++) {
      await user.click(chip);
      seen.push(expanded().getAttribute('aria-label'));
    }
    // The rank, not the age: by raisedAt alone the question would come first.
    // Plan review, then document review, then question.
    expect(seen).toEqual([
      'Plan the order export',
      'Rotate auth tokens',
      'Shape the orchestrator UI',
    ]);
  });

  it('counts only the nodes the filters leave on the canvas', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.selectOptions(screen.getByLabelText('Project'), 'maelstrom');
    expect(screen.getByTestId('attention-chip')).toHaveAttribute('data-count', '1');
  });
});
