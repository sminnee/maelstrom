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

/** The boundary NORT-9's agent emits when a compact finishes. */
function compactBoundary(server: Awaited<ReturnType<typeof renderApp>>['server']) {
  server.append('d9a4c7f1', {
    id: 'd9a4c7f1-compact',
    ts: '',
    type: 'compact',
    trigger: 'manual',
    preTokens: 23238,
    postTokens: 3046,
  });
}

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

  it('stays busy until the boundary arrives, because the relay returns long before', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    server.change({ kind: 'agent', ids: ['d9a4c7f1'] }, (w) => {
      w.agents['d9a4c7f1'] = { ...w.agents['d9a4c7f1']!, state: 'idle' };
    });
    await openTaskSession(user);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Compact' })).toBeEnabled());

    await user.click(screen.getByRole('button', { name: 'Compact' }));

    // `say` resolves when the server accepts the relay, which is a pure
    // relay — the compaction itself takes 10s–130s after that.
    await waitFor(() => expect(screen.getByRole('button', { name: /Compacting/ })).toBeDisabled());

    compactBoundary(server);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Compact' })).toBeEnabled());
  });

  it('gives up when the agent goes idle without compacting, which is the refusal', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    server.change({ kind: 'agent', ids: ['d9a4c7f1'] }, (w) => {
      w.agents['d9a4c7f1'] = { ...w.agents['d9a4c7f1']!, state: 'idle' };
    });
    await openTaskSession(user);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Compact' })).toBeEnabled());

    await user.click(screen.getByRole('button', { name: 'Compact' }));
    await waitFor(() => expect(screen.getByRole('button', { name: /Compacting/ })).toBeDisabled());

    // "Not enough messages to compact." is ordinary assistant text followed by
    // an ordinary successful result, so the turn ending is the only signal
    // there is — and it must not be mistaken for a finished compact.
    server.append('d9a4c7f1', {
      id: 'd9a4c7f1-refused',
      ts: '',
      type: 'turn_result',
      subtype: 'success',
      costUsd: 0.01,
      durationMs: 900,
    });

    // The title carries the error, so this pins the refusal rather than the
    // mere presence of an alert — the backstop would also raise one.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Failed/ })).toHaveAttribute(
        'title',
        expect.stringContaining('did not compact'),
      ),
    );
  });

  it('gives up when the agent exits, which appends no transcript item at all', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    server.change({ kind: 'agent', ids: ['d9a4c7f1'] }, (w) => {
      w.agents['d9a4c7f1'] = { ...w.agents['d9a4c7f1']!, state: 'idle' };
    });
    await openTaskSession(user);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Compact' })).toBeEnabled());

    await user.click(screen.getByRole('button', { name: 'Compact' }));
    await waitFor(() => expect(screen.getByRole('button', { name: /Compacting/ })).toBeDisabled());

    // `mark_exited` emits no transcript item, so without reading the agent's
    // own state the button would spin out the whole five-minute backstop.
    server.change({ kind: 'agent', ids: ['d9a4c7f1'] }, (w) => {
      w.agents['d9a4c7f1'] = { ...w.agents['d9a4c7f1']!, state: 'exited', exitCode: 1 };
    });

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
  });

  it('draws the boundary in the transcript, where the operator can see it fell', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    await openTaskSession(user);

    compactBoundary(server);

    const rule = await screen.findByTestId('compact');
    expect(rule).toHaveTextContent('compacted');
    // The header's own rounding, so the rule and the header read together.
    // The unit lands once, on the figure it ends on.
    expect(rule).toHaveTextContent('23k → 3k ctx');
  });

  it('folds the summary the compact carried over, which the harness wrote', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    await openTaskSession(user);

    server.append('d9a4c7f1', {
      id: 'd9a4c7f1-carried',
      ts: '',
      type: 'compact_summary',
      markdown: 'This session is being continued from a previous conversation.\n\nSummary: …',
    });

    const folded = await screen.findByTestId('compact-summary');
    // Folded, so the body does not bury the rule directly above it.
    expect(folded.tagName).toBe('DETAILS');
    expect(folded).not.toHaveAttribute('open');
    expect(folded).toHaveTextContent('carried over');
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

/**
 * Settle the session tab on seeded content before moving the world.
 *
 * Scoped to the tab: the expanded node card behind the panel draws the same
 * last message, so an unscoped query matches twice. A wait on the first item
 * over a freshly-opened socket is what flaked a group out of the suite before.
 */
async function settleOnSeed() {
  const tab = screen.getByTestId('session-tab');
  await within(tab).findByText('Rewriting the migration for the new collation.');
  return tab;
}

/** Append `count` assistant messages to NORT-9's agent, oldest first. */
function appendMany(
  server: Awaited<ReturnType<typeof renderApp>>['server'],
  count: number,
  label = 'event',
) {
  for (let i = 0; i < count; i += 1) {
    server.append('d9a4c7f1', {
      id: `d9a4c7f1-${label}-${i}`,
      ts: '',
      type: 'message',
      role: 'assistant',
      markdown: `${label} ${i}`,
    });
  }
}

describe('the transcript window', () => {
  it('draws the recent events and offers the rest, so a long session opens promptly', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    await openTaskSession(user);
    await settleOnSeed();

    appendMany(server, 60);

    // 64 items in all, so the oldest are held back rather than drawn.
    await waitFor(() => expect(screen.getAllByTestId('transcript-card')).toHaveLength(50));
    expect(screen.getByRole('button', { name: /earlier events/ })).toBeInTheDocument();
    expect(screen.queryByText('event 0')).toBeNull();
    expect(screen.getByText('event 59')).toBeInTheDocument();
  });

  it('keeps a revealed event on screen when the agent speaks again', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    await openTaskSession(user);
    await settleOnSeed();

    appendMany(server, 60);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /earlier events/ })).toBeVisible(),
    );

    await user.click(screen.getByRole('button', { name: /earlier events/ }));
    expect(await screen.findByText('event 0')).toBeInTheDocument();

    // The window holds an absolute floor, not a count: one more event extends
    // the bottom rather than dropping the oldest revealed row off the top.
    appendMany(server, 1, 'later');
    expect(await screen.findByText('later 0')).toBeInTheDocument();
    expect(screen.getByText('event 0')).toBeInTheDocument();
  });
});

describe('the stop button', () => {
  it('abandons the turn the agent is running, and keeps the agent', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    // NORT-9's agent is processing in the seed, so this one needs no mutation.
    await openTaskSession(user);
    const stop = screen.getByRole('button', { name: 'Stop' });
    expect(stop).toHaveAttribute(
      'title',
      'Abandon the turn the agent is running. The agent stays alive.',
    );

    await user.click(stop);

    await waitFor(() =>
      expect(server.requests.find((r) => r.path.endsWith('/interrupt'))).toBeDefined(),
    );
    // Alive, not exited: an exited agent leaves Compact disabled, so this is
    // the assertion that separates Stop from the node card's Terminate.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Compact' })).toBeEnabled());
    expect(screen.getByRole('button', { name: 'Stop' })).toHaveAttribute(
      'title',
      'The agent is not running a turn.',
    );
  });

  it('is not offered once the agent is idle, which has no turn to abandon', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    server.change({ kind: 'agent', ids: ['d9a4c7f1'] }, (w) => {
      w.agents['d9a4c7f1'] = { ...w.agents['d9a4c7f1']!, state: 'idle' };
    });
    await openTaskSession(user);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Stop' })).toBeDisabled());
    expect(screen.getByRole('button', { name: 'Stop' })).toHaveAttribute(
      'title',
      'The agent is not running a turn.',
    );
  });

  it('is not offered while the agent waits on a person, whose ask comes first', async () => {
    const user = userEvent.setup();
    await renderApp();
    // MAEL-52's agent is blocked on a question in the seed.
    clickNode('MAEL-52');
    await user.click(within(screen.getByRole('dialog')).getByRole('link', { name: 'Session' }));

    const stop = screen.getByRole('button', { name: 'Stop' });
    expect(stop).toBeDisabled();
    expect(stop).toHaveAttribute(
      'title',
      'The agent is waiting on you. Answer or deny the ask instead.',
    );
  });

  it('is not offered once the agent has exited, which can take nothing', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    server.change({ kind: 'agent', ids: ['d9a4c7f1'] }, (w) => {
      w.agents['d9a4c7f1'] = { ...w.agents['d9a4c7f1']!, state: 'exited', exitCode: 0 };
    });
    await openTaskSession(user);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Stop' })).toBeDisabled());
    // Exited is tested before the state, so this title wins over "not running
    // a turn" — an exited agent is both.
    expect(screen.getByRole('button', { name: 'Stop' })).toHaveAttribute(
      'title',
      'The agent has exited.',
    );
  });

  it('is not offered to a subagent, which has no pipe of its own', async () => {
    const user = userEvent.setup();
    await renderApp();
    await openTaskSession(user);
    await user.click(screen.getByRole('link', { name: /Find every collation-sensitive query/ }));

    expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument();
  });
});
