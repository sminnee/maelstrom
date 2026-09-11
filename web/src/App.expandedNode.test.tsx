import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import { act } from 'react';
import userEvent from '@testing-library/user-event';
import type { Agent } from './protocol/entities';
import { TASK_STATUSES } from './protocol/entities';
import type { FakeServer } from './test/fakeServer';
import { askQuestion, chipCount, expanded, nodeState } from './test/appHelpers';
import { clickNode, pressKey, renderApp } from './test/renderApp';

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

  describe('external links', () => {
    it('links the PR at its own URL, saying its state, in a new tab', async () => {
      await renderApp();
      clickNode('NORT-12');
      const link = within(expanded()).getByRole('link', { name: 'PR #118, CI running' });
      expect(link).toHaveAttribute('href', 'https://github.com/acme/northwind/pull/118');
      expect(link).toHaveAttribute('target', '_blank');
    });

    it('follows the PR state as CI finishes, without a reload', async () => {
      const { server } = await renderApp();
      clickNode('NORT-12');
      expect(
        within(expanded()).getByRole('link', { name: 'PR #118, CI running' }),
      ).toBeInTheDocument();
      act(() => {
        server.change({ kind: 'worktree', ids: ['northwind-delta'] }, (w) => {
          w.worktrees['northwind-delta'] = {
            ...w.worktrees['northwind-delta']!,
            prState: 'ready',
          };
        });
      });
      await waitFor(() =>
        expect(
          within(expanded()).getByRole('link', { name: 'PR #118, ready to merge' }),
        ).toBeInTheDocument(),
      );
    });

    it('says a draft PR is a draft, whatever its checks are doing', async () => {
      const { server } = await renderApp();
      clickNode('NORT-12');
      act(() => {
        server.change({ kind: 'worktree', ids: ['northwind-delta'] }, (w) => {
          w.worktrees['northwind-delta'] = {
            ...w.worktrees['northwind-delta']!,
            prDraft: true,
          };
        });
      });
      await waitFor(() =>
        expect(
          within(expanded()).getByRole('link', { name: 'PR #118, draft' }),
        ).toBeInTheDocument(),
      );
    });

    it('links the dev env only while it runs, and drops the link when it stops', async () => {
      const { server } = await renderApp();
      clickNode('NORT-12');
      expect(within(expanded()).getByRole('link', { name: 'Dev env' })).toHaveAttribute(
        'href',
        'http://localhost:4210',
      );
      act(() => {
        server.change({ kind: 'worktree', ids: ['northwind-delta'] }, (w) => {
          w.worktrees['northwind-delta'] = {
            ...w.worktrees['northwind-delta']!,
            appRunning: false,
          };
        });
      });
      await waitFor(() =>
        expect(within(expanded()).queryByRole('link', { name: 'Dev env' })).toBeNull(),
      );
      // The PR link is not the dev env's: it stays.
      expect(
        within(expanded()).getByRole('link', { name: 'PR #118, CI running' }),
      ).toBeInTheDocument();
    });
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

  it('answers a question that sits behind a newer permission, and approves the permission', async () => {
    // The agent reports the newer permission while the question is still open,
    // so each reply must be judged by the request it names — see CONTEXT.md,
    // "Wait kind".
    const user = userEvent.setup();
    const { server } = await renderApp();
    server.append('b7d2e4a0', {
      id: 'b7d2e4a0-p',
      ts: '',
      type: 'permission_request',
      requestId: 'req-mael52-p',
      tool: 'Bash',
      input: { command: 'pnpm test' },
      description: 'Run the web suite',
    });
    server.change({ kind: 'agent', ids: ['b7d2e4a0'] }, (w) => {
      w.agents['b7d2e4a0'] = {
        ...w.agents['b7d2e4a0']!,
        state: 'awaiting-permission',
        pendingRequestIds: ['req-mael52-q', 'req-mael52-p'],
      };
    });
    clickNode('MAEL-52');
    const card = expanded();

    // The permission is the one the agent's state names; the question is not.
    const prompt = await within(card).findByTestId('question-prompt');
    await user.click(within(prompt).getAllByRole('radio')[0]!);
    await user.click(within(prompt).getByRole('button', { name: 'Answer' }));
    await waitFor(() =>
      expect(within(card).queryByTestId('question-prompt')).not.toBeInTheDocument(),
    );

    await user.click(await within(card).findByRole('button', { name: 'Approve' }));
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

describe('how long since the agent spoke', () => {
  /** Put NORT-9's agent's last message `minutesAgo`, in whatever `state`. */
  function spokeAt(server: FakeServer, minutesAgo: number, state: Agent['state']) {
    server.change({ kind: 'agent', ids: ['d9a4c7f1'] }, (w) => {
      const agent = w.agents['d9a4c7f1']!;
      agent.lastMessageAt = new Date(Date.now() - minutesAgo * 60_000).toISOString();
      agent.state = state;
    });
  }

  it('shows the age of the last message beside the heading', async () => {
    const { server } = await renderApp();
    spokeAt(server, 12, 'processing');
    clickNode('NORT-9');
    await waitFor(() =>
      expect(within(expanded()).getByTestId('now-age')).toHaveTextContent('12m ago'),
    );
  });

  it('shows no age at all for an agent that has said nothing', async () => {
    const { server } = await renderApp();
    server.change({ kind: 'agent', ids: ['d9a4c7f1'] }, (w) => {
      w.agents['d9a4c7f1']!.lastMessageAt = '';
    });
    clickNode('NORT-9');
    await waitFor(() => expect(within(expanded()).queryByTestId('now-age')).toBeNull());
  });

  it('marks a working agent once it has been silent ten minutes', async () => {
    const { server } = await renderApp();
    spokeAt(server, 11, 'processing');
    clickNode('NORT-9');
    await waitFor(() =>
      expect(within(expanded()).getByTestId('now-age').closest('[data-silent]')).not.toBeNull(),
    );
  });

  it('leaves an idle agent unmarked however long it has been silent', async () => {
    const { server } = await renderApp();
    spokeAt(server, 240, 'idle');
    clickNode('NORT-9');
    await waitFor(() => {
      const age = within(expanded()).getByTestId('now-age');
      expect(age).toHaveTextContent('4h ago');
      expect(age.closest('[data-silent]')).toBeNull();
    });
  });
});

describe('the state in words', () => {
  it('the session tab names the wait when its agent is blocked on the user', async () => {
    const { server } = await renderApp();
    askQuestion(server);
    clickNode('NORT-9');
    await userEvent.setup().click(await within(expanded()).findByRole('link', { name: 'Session' }));
    await waitFor(() =>
      expect(screen.getByRole('tabpanel')).toHaveTextContent('Needs you · question'),
    );
  });
});

describe('drift between the task file and the agent', () => {
  /** Stop MAEL-40.1's agent cleanly, leaving the task in-progress. */
  function stopAgent(server: FakeServer) {
    server.change({ kind: 'agent', ids: ['c3e8f1b5'] }, (w) => {
      w.agents['c3e8f1b5'] = { ...w.agents['c3e8f1b5']!, state: 'exited', exitCode: 0 };
    });
  }

  it('marks a drifting node without escalating it', async () => {
    const { server } = await renderApp();
    const node = () => document.querySelector('[data-task-id="MAEL-40.1"]')!;
    expect(node().querySelector('[data-drift]')).toBeNull();

    stopAgent(server);
    await waitFor(() => expect(node().querySelector('[data-drift]')).not.toBeNull());
    expect(node().querySelector('[data-drift]')).toHaveAttribute('data-drift', 'finished');
    // Drift is a channel, not a state of its own: the Single Interrupt Rule
    // keeps the amber border and glow for work that is really blocked.
    expect(nodeState('MAEL-40.1')).toBe('stopped');
  });

  it('names both values on the card, and its fix moves the task', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    stopAgent(server);
    await waitFor(() =>
      expect(document.querySelector('[data-task-id="MAEL-40.1"] [data-drift]')).not.toBeNull(),
    );
    clickNode('MAEL-40.1');

    const band = within(expanded()).getByTestId('drift-band');
    expect(band).toHaveTextContent('The agent has stopped, but the task is still in-progress.');

    await user.click(within(band).getByRole('button', { name: 'Mark done' }));
    // The status it sends is the fix, not merely that it sent one: posting
    // `todo` here would send finished work back to the queue.
    await waitFor(() => {
      const posted = server.requests.find(
        (r) => r.method === 'POST' && r.path.includes('MAEL-40.1') && r.path.endsWith('/status'),
      );
      expect(posted?.body).toEqual({ status: 'done' });
    });
  });
});
