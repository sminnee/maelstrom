import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { clickNode, renderApp } from '../test/renderApp';

const head = () => screen.getByTestId('session-head');

/** Open the session of the seed's free agent, which runs in `maelstrom-bravo`. */
async function openFreeAgentSession(user: ReturnType<typeof userEvent.setup>) {
  clickNode('f2c6a9d4');
  await user.click(within(screen.getByRole('dialog')).getByRole('link', { name: 'Session' }));
  return screen.getByTestId('session-tab');
}

/** Open a task agent's session: NORT-9, which runs in `northwind-bravo`. */
async function openTaskSession(user: ReturnType<typeof userEvent.setup>) {
  clickNode('NORT-9');
  await user.click(within(screen.getByRole('dialog')).getByRole('link', { name: 'Session' }));
  return screen.getByTestId('session-tab');
}

describe('the session header', () => {
  it('names where a free agent runs, which nothing else on its session says', async () => {
    const user = userEvent.setup();
    await renderApp();
    await openFreeAgentSession(user);

    // The worktree carries the project, so the two are one field.
    expect(head()).toHaveTextContent('maelstrom-bravo');
    expect(head()).toHaveTextContent('feat/task-index');
    // The agent reports the resolved id; the header says the alias.
    expect(head()).toHaveTextContent('opus');
    expect(head()).not.toHaveTextContent('claude-opus-5');
  });

  it('puts the live reading and the standing context on one row', async () => {
    const user = userEvent.setup();
    await renderApp();
    await openFreeAgentSession(user);

    // One row, so a wide panel spends its width rather than its height. The
    // fallback to two rows is a container query, which jsdom cannot compute —
    // see the verification note in web/DESIGN.md, "Session header".
    const rows = head().querySelectorAll('[data-testid="session-head-row"]');
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent('f2c6a9d4');
    expect(rows[0]).toHaveTextContent('maelstrom-bravo');
    expect(within(rows[0] as HTMLElement).getByRole('button', { name: 'Compact' })).toBeVisible();
  });

  it('says how full the context is and what the session has spent', async () => {
    const user = userEvent.setup();
    await renderApp();
    await openFreeAgentSession(user);

    // 12,300 tokens of context and $0.19 in the seed.
    expect(head()).toHaveTextContent('12k ctx');
    expect(head()).toHaveTextContent('$0.19');
  });

  it('reports the context rather than the work the session has done', async () => {
    const user = userEvent.setup();
    await renderApp();
    await openTaskSession(user);

    // NORT-9's agent has run up 1,240,000 tokens but holds 152,000 of context.
    // The larger number is the one a reader must not mistake for the window.
    expect(head()).toHaveTextContent('152k ctx');
    expect(head()).not.toHaveTextContent('1.2M');
  });

  it('follows the context down when the agent compacts', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    await openTaskSession(user);
    expect(head()).toHaveTextContent('152k ctx');

    // A compact is the whole point of showing the number: it must fall.
    server.change({ kind: 'agent', ids: ['d9a4c7f1'] }, (w) => {
      w.agents['d9a4c7f1'] = { ...w.agents['d9a4c7f1']!, contextTokens: 18_000 };
    });
    await waitFor(() => expect(head()).toHaveTextContent('18k ctx'));
  });

  it('says nothing about a context or a cost an agent has not run up yet', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    server.change({ kind: 'agent', ids: ['f2c6a9d4'] }, (w) => {
      w.agents['f2c6a9d4'] = { ...w.agents['f2c6a9d4']!, contextTokens: 0, costUsd: 0 };
    });
    await openFreeAgentSession(user);

    await waitFor(() => expect(head()).not.toHaveTextContent('ctx'));
    expect(head()).not.toHaveTextContent('$');
    // The worktree is still there: only the empty fields drop out.
    expect(head()).toHaveTextContent('maelstrom-bravo');
  });

  it('still names the worktree the world has not read yet', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    // A worktree the poll has not reached: the agent knows its id regardless.
    server.change({ kind: 'worktree', ids: [] }, (w) => {
      delete w.worktrees['maelstrom-bravo'];
    });
    await openFreeAgentSession(user);

    await waitFor(() => expect(head()).toHaveTextContent('maelstrom-bravo'));
    expect(head()).toHaveTextContent('opus');
  });

  it('gives a subagent no metadata, because it has no session of its own', async () => {
    const user = userEvent.setup();
    await renderApp();
    await openTaskSession(user);
    // The strip under the transcript is the only way into a subagent's tab.
    await user.click(screen.getByRole('link', { name: /Find every collation-sensitive query/ }));

    expect(head()).toHaveTextContent('d9a4c7f1.1');
    expect(head()).not.toHaveTextContent('ctx');
    expect(head()).not.toHaveTextContent('northwind-bravo');
  });
});

describe('the compact button', () => {
  it('asks the agent to compact, which is its own command and not a UI fold', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    // Idle first: the seed's agent is mid-turn, and a working agent is not
    // asked to compact — see the test below.
    server.change({ kind: 'agent', ids: ['d9a4c7f1'] }, (w) => {
      w.agents['d9a4c7f1'] = { ...w.agents['d9a4c7f1']!, state: 'idle' };
    });
    await openTaskSession(user);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Compact' })).toBeEnabled());

    await user.click(screen.getByRole('button', { name: 'Compact' }));

    await waitFor(() => {
      const said = server.requests.find((r) => r.path.endsWith('/say'));
      expect(said).toBeDefined();
      expect((said!.body as { text: string }).text).toBe('/compact');
    });
  });

  it('is not offered while the agent owes a turn, which would queue it behind the work', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    await openTaskSession(user);
    // NORT-9's agent is processing in the seed.
    expect(screen.getByRole('button', { name: 'Compact' })).toBeDisabled();

    server.change({ kind: 'agent', ids: ['d9a4c7f1'] }, (w) => {
      w.agents['d9a4c7f1'] = { ...w.agents['d9a4c7f1']!, state: 'idle' };
    });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Compact' })).toBeEnabled());
  });

  it('is not offered to an exited agent, which can take nothing', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    server.change({ kind: 'agent', ids: ['d9a4c7f1'] }, (w) => {
      w.agents['d9a4c7f1'] = { ...w.agents['d9a4c7f1']!, state: 'exited', exitCode: 0 };
    });
    await openTaskSession(user);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Compact' })).toBeDisabled());
  });

  it('is not offered while the agent waits on a person, whose ask comes first', async () => {
    const user = userEvent.setup();
    await renderApp();
    // MAEL-52's agent is blocked on a question in the seed.
    clickNode('MAEL-52');
    await user.click(within(screen.getByRole('dialog')).getByRole('link', { name: 'Session' }));

    expect(screen.getByRole('button', { name: 'Compact' })).toBeDisabled();
  });

  it('is not offered to a subagent, which has no pipe of its own', async () => {
    const user = userEvent.setup();
    await renderApp();
    await openTaskSession(user);
    await user.click(screen.getByRole('link', { name: /Find every collation-sensitive query/ }));

    expect(screen.queryByRole('button', { name: 'Compact' })).not.toBeInTheDocument();
  });
});
