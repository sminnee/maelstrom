import { describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
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
 * last message, so an unscoped query matches twice.
 */
async function settleOnSeed() {
  const tab = screen.getByTestId('session-tab');
  await within(tab).findByText('Rewriting the migration for the new collation.');
  return tab;
}

/** The seeded transcript NORT-9's agent opens with. */
const SEEDED_ITEMS = 4;

/** The markdown of every drawn card, top to bottom. */
function drawnRows() {
  return screen.getAllByTestId('transcript-card').map((c) => c.textContent ?? '');
}

/**
 * Watch the jump the transcript makes to follow the tail.
 *
 * jsdom implements no `scrollIntoView` — which is why the call site guards it
 * with `?.` — and `vi.spyOn` cannot wrap a method that is not there, so the
 * property is defined before the spy replaces it.
 */
function watchScroll() {
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    writable: true,
    value: () => {},
  });
  return vi.spyOn(HTMLElement.prototype, 'scrollIntoView');
}

/**
 * Scroll the transcript container to its tail, or away from it.
 *
 * The geometry is defined on the container node, not on `HTMLElement`'s
 * prototype: `useClamped` measures every element it is given, so a prototype
 * spy silently forces the node card behind the panel into its clamped state
 * and the test asserts against a DOM whose layout is globally false.
 */
function scrollTranscriptTo(where: 'bottom' | 'up') {
  const el = screen.getByTestId('transcript-scroll');
  const geometry = { scrollHeight: 1000, clientHeight: 200 };
  for (const [prop, value] of Object.entries(geometry)) {
    Object.defineProperty(el, prop, { configurable: true, value });
  }
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    value: where === 'bottom' ? 800 : 100,
  });
  fireEvent.scroll(el);
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

    const appended = 60;
    appendMany(server, appended);

    // The window opens on the tail, so the newest 50 draw and everything
    // older is held back — including the last of the seeded rows.
    await waitFor(() => expect(drawnRows()).toHaveLength(50));
    const held = SEEDED_ITEMS + appended - 50;
    expect(screen.getByRole('button', { name: `Show ${held} earlier events` })).toBeVisible();
    expect(drawnRows().at(0)).toContain(`event ${appended - 50}`);
    expect(drawnRows().at(-1)).toContain(`event ${appended - 1}`);
  });

  it('offers nothing while the whole session fits in one window', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    await openTaskSession(user);
    await settleOnSeed();

    // The clamp: fewer items than the window means no floor to hold and
    // nothing to reveal, which is what every short session hits.
    appendMany(server, 10);
    await waitFor(() => expect(drawnRows()).toHaveLength(SEEDED_ITEMS + 10));
    expect(screen.queryByRole('button', { name: /earlier events/ })).toBeNull();
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

    // The window anchors on the oldest revealed event, not on a count: one
    // more event extends the bottom rather than dropping that row off the top.
    appendMany(server, 1, 'later');
    expect(await screen.findByText('later 0')).toBeInTheDocument();
    expect(screen.getByText('event 0')).toBeInTheDocument();
  });

  it('holds the revealed events when a reconnect re-snapshots the transcript', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    await openTaskSession(user);
    await settleOnSeed();

    // 124 items, so one reveal lands mid-transcript rather than at its head:
    // the anchor is then an event a front-drop can shift without removing.
    appendMany(server, 120);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /earlier events/ })).toBeVisible(),
    );
    await user.click(screen.getByRole('button', { name: /earlier events/ }));
    expect(await screen.findByText('event 20')).toBeInTheDocument();
    expect(drawnRows().at(0)).toContain('event 20');

    // A lagging reconnect replaces `items` wholesale, and the host drops from
    // the front of its own list past its cap. That moves every surviving index
    // by three: an index-based floor would reopen three events further down,
    // where the id the reader opened on still names theirs.
    server.resnapshot('d9a4c7f1', 121, { dropFront: 3 });

    await waitFor(() => expect(screen.queryByText('Migrate to Postgres 16.')).toBeNull());
    expect(drawnRows().at(0)).toContain('event 20');
  });

  it('falls back to the tail when the revealed event is dropped from the front', async () => {
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

    // The anchor itself is gone from the new snapshot. The window has nothing
    // to hold onto, so it reopens on the tail rather than on a wrong event.
    server.resnapshot('d9a4c7f1', 20, { dropFront: 30 });

    await waitFor(() => expect(screen.queryByText('event 0')).toBeNull());
    expect(drawnRows().length).toBeLessThanOrEqual(50);
  });
});

describe('opening another agent in the same tab', () => {
  it('follows the new agent’s tail, whatever the last agent’s reader was doing', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    await openTaskSession(user);
    await settleOnSeed();

    // The scroll position is what carries across an agent change, because the
    // tab reads it from a ref rather than from the transcript. Leave the first
    // agent scrolled up, which is the state that must not be inherited.
    const scrolled = watchScroll();
    scrollTranscriptTo('up');
    appendMany(server, 1, 'parent');
    await screen.findByText('parent 0');
    expect(scrolled).not.toHaveBeenCalled();

    // The subagent strip is the way into another agent's session.
    await user.click(screen.getByRole('link', { name: /Find every collation-sensitive query/ }));
    expect(await screen.findByText('Grep for ORDER BY name.')).toBeInTheDocument();

    // A reader who has never scrolled this agent is at its tail, so it follows.
    server.append('d9a4c7f1.1', {
      id: 'd9a4c7f1.1-said',
      ts: '',
      type: 'message',
      role: 'assistant',
      markdown: 'child 0',
    });
    await screen.findByText('child 0');
    expect(scrolled).toHaveBeenCalled();
  });
});

describe('following the transcript', () => {
  it('does not drag the reader down when they are reading history', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    await openTaskSession(user);
    await settleOnSeed();

    const scrolled = watchScroll();
    // Away from the tail first, then back to it: one scenario, so the two
    // assertions differ only in where the reader sits. Without the second
    // half, a guard that never followed at all would pass the first.
    scrollTranscriptTo('up');
    appendMany(server, 1, 'interrupting');
    await screen.findByText('interrupting 0');
    expect(scrolled).not.toHaveBeenCalled();

    scrollTranscriptTo('bottom');
    appendMany(server, 1, 'resumed');
    await screen.findByText('resumed 0');
    expect(scrolled).toHaveBeenCalled();
  });

  it('follows the tail again once the reader returns to the bottom', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    await openTaskSession(user);
    await settleOnSeed();

    const scrolled = watchScroll();
    scrollTranscriptTo('bottom');

    appendMany(server, 1, 'following');
    await screen.findByText('following 0');
    expect(scrolled).toHaveBeenCalled();
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
