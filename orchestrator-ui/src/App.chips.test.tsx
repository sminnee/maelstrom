import { describe, expect, it } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { act } from 'react';
import userEvent from '@testing-library/user-event';
import { endTurn } from './fake/moves';
import { chipCount, expanded, nodeState, unansweredCount, paneItem } from './test/appHelpers';
import { renderApp } from './test/renderApp';
import agentsChipStyles from './shell/AgentsChip.module.css';

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
    await waitFor(() =>
      expect(screen.getByLabelText(/5-hour limit: 7% consumed/)).toBeInTheDocument(),
    );
    expect(screen.getByLabelText(/7-day limit: 24% consumed/)).toBeInTheDocument();
  });

  it('counts the agents that are working over those that are open', async () => {
    await renderApp();
    // The seed is deterministic: six top-level agents, four of them mid-turn.
    // The literal is what makes this catch a miscount -- a regex over the
    // shape would pass on "0 of 0" and on any wrong arithmetic.
    expect(await screen.findByLabelText(/^4 of 6 agents working, 2 idle\./)).toBeInTheDocument();
  });

  it('draws the count in the plain text colour while agents work', async () => {
    await renderApp();
    // Agents at work is the normal state, not news: a toned count would be lit
    // all day and mean nothing.
    expect(await screen.findByLabelText(/agents working/)).toHaveAttribute('data-tone', 'neutral');
  });
});

describe('the agents chip as a filter', () => {
  const agentStatus = () => (screen.getByLabelText('Agent status') as HTMLSelectElement).value;
  const chip = () => screen.getByLabelText(/agents working/);
  /** The value spans the stylesheet greys out, by their text. */
  const faint = () =>
    Array.from(chip().querySelectorAll(`.${agentsChipStyles.faint}`)).map((n) => n.textContent);
  const reading = () => ({ status: agentStatus(), faint: faint(), name: chip().ariaLabel });
  const counts = '4 of 6 agents working, 2 idle';

  it('steps the agent status filter through all, working + idle and working', async () => {
    const user = userEvent.setup();
    await renderApp();
    const seen = [reading()];
    for (let i = 0; i < 3; i++) {
      await user.click(chip());
      seen.push(reading());
    }
    expect(seen).toEqual([
      {
        status: 'all',
        faint: [],
        name: `${counts}. Agent status: All. Click to show Working + Idle.`,
      },
      {
        status: 'working-idle',
        faint: ['4/'],
        name: `${counts}. Agent status: Working + Idle. Click to show Working.`,
      },
      {
        status: 'working',
        faint: ['/6'],
        name: `${counts}. Agent status: Working. Click to show All.`,
      },
      {
        status: 'all',
        faint: [],
        name: `${counts}. Agent status: All. Click to show Working + Idle.`,
      },
    ]);
  });

  it('filters the desk: working + idle leaves out a task with no agent', async () => {
    const user = userEvent.setup();
    await renderApp();
    expect(document.querySelector('[data-task-id="NORT-15"]')).toBeInTheDocument();
    await user.click(chip());
    expect(document.querySelector('[data-task-id="NORT-15"]')).not.toBeInTheDocument();
  });

  it('names a status outside its cycle, and steps from it as from all', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.selectOptions(screen.getByLabelText('Agent status'), 'planned');
    expect(reading()).toEqual({
      status: 'planned',
      faint: [],
      name: `${counts}. Agent status: Planned. Click to show Working + Idle.`,
    });
    await user.click(chip());
    expect(agentStatus()).toBe('working-idle');
  });

  it('is a plain reading while the desk is off screen', async () => {
    const user = userEvent.setup();
    await renderApp();
    await user.click(chip());
    await user.click(chip());
    await user.click(paneItem('Tasks'));
    expect(screen.queryByRole('button', { name: /agents working/ })).toBeNull();
    // The filter still holds `working`, but the chip neither greys nor names it.
    expect(faint()).toEqual([]);
    expect(chip()).toHaveAccessibleName(counts);
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

  /** The nodes the canvas draws orange, by id. */
  const orange = () =>
    Array.from(document.querySelectorAll('[data-testid="task-node"][data-state="needs-attention"]'))
      .map((n) => n.getAttribute('data-task-id'))
      .sort();

  it('counts the orange nodes, under a filter too', async () => {
    const user = userEvent.setup();
    await renderApp();
    expect(orange()).toEqual(['MAEL-52', 'NORT-12', 'NORT-7']);
    expect(chipCount()).toBe(3);

    await user.selectOptions(screen.getByLabelText('Project'), 'maelstrom');
    expect(orange()).toEqual(['MAEL-52']);
    expect(chipCount()).toBe(1);

    // The agent status filter draws no waiting agent, so nothing is orange.
    await user.selectOptions(screen.getByLabelText('Project'), '');
    await user.selectOptions(screen.getByLabelText('Agent status'), 'planned');
    expect(orange()).toEqual([]);
    expect(chipCount()).toBe(0);
  });

  it('counts the unanswered nodes apart from the asks, and goes to one when no ask is open', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const chip = screen.getByTestId('attention-chip');
    expect(unansweredCount()).toBe(0);
    expect(chip).toHaveAccessibleName('3 items need attention');

    act(() => endTurn(server));
    await waitFor(() => expect(nodeState('MAEL-40.1')).toBe('unanswered'));
    expect(chipCount()).toBe(3);
    expect(unansweredCount()).toBe(1);
    expect(chip).toHaveAccessibleName('3 items need attention, 1 agent unanswered');

    // Every open ask is cleared, so the unanswered node is all the chip holds.
    act(() => {
      server.change({ kind: 'attention', ids: [] }, (w) => {
        for (const item of Object.values(w.attention)) item.clearedAt = '2026-09-01T00:00:00Z';
      });
    });
    await waitFor(() => expect(chipCount()).toBe(0));
    expect(unansweredCount()).toBe(1);
    expect(chip).toBeEnabled();
    await user.click(chip);
    expect(expanded()).toHaveAccessibleName('Restamp the index on HEAD change');
  });

  it('leaves out a node whose agent exited nonzero, and never lands on it', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    // NORT-7 holds the top-ranked item, so the old reading went there first.
    act(() => {
      server.change({ kind: 'agent', ids: ['a1f3c9e2'] }, (w) => {
        w.agents['a1f3c9e2'] = { ...w.agents['a1f3c9e2']!, state: 'exited', exitCode: 1 };
      });
    });
    await waitFor(() => expect(orange()).toEqual(['MAEL-52', 'NORT-12']));
    expect(chipCount()).toBe(2);

    const seen: (string | null)[] = [];
    for (let i = 0; i < 3; i++) {
      await user.click(screen.getByTestId('attention-chip'));
      seen.push(expanded().getAttribute('aria-label'));
    }
    expect(seen).toEqual(['Rotate auth tokens', 'Shape the orchestrator UI', 'Rotate auth tokens']);
  });

  it('counts a free agent that waits on the user, and lands on it', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    act(() => {
      server.change({ kind: 'attention', ids: ['att-free-plan'] }, (w) => {
        w.attention['att-free-plan'] = {
          id: 'att-free-plan',
          kind: 'plan_review',
          agentId: 'f2c6a9d4',
          taskId: null,
          documentId: null,
          requestId: 'req-free-plan',
          summary: 'Plan awaiting review',
          // Older than NORT-7's plan review, so it ranks first.
          raisedAt: '2000-01-01T00:00:00Z',
          clearedAt: null,
        };
      });
    });
    await waitFor(() => expect(orange()).toContain('f2c6a9d4'));
    expect(chipCount()).toBe(4);

    await user.click(screen.getByTestId('attention-chip'));
    expect(expanded()).toHaveAccessibleName('bravo · feat/task-index');
  });
});
