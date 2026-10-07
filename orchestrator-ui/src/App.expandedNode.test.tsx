import { describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import { act } from 'react';
import userEvent from '@testing-library/user-event';
import type { Agent, Worktree } from './protocol/entities';
import { TASK_STATUSES } from './protocol/entities';
import type { FakeServer } from './fake/fakeServer';
import type { TranscriptItem } from './protocol/transcript';
import { askQuestion, endTurn } from './fake/moves';
import { chipCount, expanded, nodeState, tabBody, worktreeControls } from './test/appHelpers';
import { clickNode, pressKey, renderApp } from './test/renderApp';
import { T } from './fake/seedWorld';

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
    it('keeps action links together and gives each document link its own row', async () => {
      await renderApp();
      clickNode('NORT-12');

      const card = expanded();
      const actions = within(card).getByTestId('node-actions');
      expect(
        within(actions)
          .getAllByRole('link')
          .map((l) => l.textContent),
      ).toEqual(['Session']);

      const documents = within(card).getByTestId('node-documents');
      expect(within(documents).getAllByRole('link')).toHaveLength(2);
      // Each link is listed under a kind heading, not on the action line.
      const listed = within(documents)
        .getAllByRole('region')
        .flatMap((section) => within(section).getAllByRole('link'));
      expect(listed).toEqual(within(documents).getAllByRole('link'));
    });

    // One area holds what belongs to the worktree, not to the agent. The
    // Worktree card is compared against it in App.worktreeCard.test.tsx.
    it('keeps the worktree, its links and its commands in one area', async () => {
      await renderApp();
      clickNode('NORT-12');
      const area = within(expanded()).getByRole('region', { name: 'Worktree' });

      expect(within(area).getByTestId('worktree-name')).toHaveTextContent(
        'delta · feat/rotate-auth-tokens-for-every-service',
      );
      // The agent of NORT-12 still runs in delta, so the card offers no close.
      // The agent's own commands are not here.
      expect(worktreeControls(expanded())).toEqual([
        ['Changes', false],
        ['PR #118, CI running', false],
        ['Dev env', false],
        ['cmux', false],
        ['Sync', false],
        ['More sync actions', false],
        ['Stop env', false],
        ['More actions', false],
      ]);
    });

    it('draws no worktree area on a node with no worktree', async () => {
      await renderApp();
      clickNode('NORT-15');
      expect(within(expanded()).queryByRole('region', { name: 'Worktree' })).toBeNull();
    });

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
      const user = userEvent.setup();
      await renderApp();
      clickNode('NORT-12');
      expect(within(expanded()).getByRole('link', { name: 'Dev env' })).toHaveAttribute(
        'href',
        '#panel/devenv/northwind-delta/web',
      );
      await user.click(within(expanded()).getByRole('button', { name: 'Stop env' }));
      await waitFor(() =>
        expect(within(expanded()).queryByRole('link', { name: 'Dev env' })).toBeNull(),
      );
      expect(within(expanded()).getByRole('button', { name: 'Start env' })).toBeInTheDocument();
      // The PR link is not the dev env's: it stays.
      expect(
        within(expanded()).getByRole('link', { name: 'PR #118, CI running' }),
      ).toBeInTheDocument();
    });

    it("links cmux at the worktree's terminal", async () => {
      await renderApp();
      clickNode('NORT-12');
      const link = within(expanded()).getByRole('link', { name: 'cmux' });
      expect(link).toHaveAttribute('href', 'cmux://workspace/WS-DELTA/pane/PANE-DELTA');
    });

    it('creates the terminal when there is none, opens it, and then links it', async () => {
      const user = userEvent.setup();
      // jsdom's `location` cannot be spied on, and it cannot follow `cmux:`.
      const assign = vi.fn();
      vi.stubGlobal('location', { ...window.location, assign });
      const { server } = await renderApp();
      clickNode('NORT-7');
      expect(within(expanded()).queryByRole('link', { name: 'cmux' })).toBeNull();

      await user.click(within(expanded()).getByRole('button', { name: 'cmux' }));

      const url = 'cmux://workspace/WS-ALPHA/pane/PANE-ALPHA';
      expect(server.requests).toContainEqual(
        expect.objectContaining({
          method: 'POST',
          path: '/api/worktrees/northwind-alpha/terminal',
        }),
      );
      await waitFor(() => expect(assign).toHaveBeenCalledWith(url));
      expect(within(expanded()).getByRole('link', { name: 'cmux' })).toHaveAttribute('href', url);
    });

    it('a refused create shows Failed and follows no link', async () => {
      const user = userEvent.setup();
      const assign = vi.fn();
      vi.stubGlobal('location', { ...window.location, assign });
      const { server } = await renderApp();
      server.refuse(/POST \/api\/worktrees\/[^/]+\/terminal$/, {
        status: 400,
        code: 'invalid',
        message: 'cmux could not make the terminal',
      });
      clickNode('NORT-7');

      await user.click(within(expanded()).getByRole('button', { name: 'cmux' }));

      const failed = await within(expanded()).findByRole('button', { name: 'Failed' });
      expect(failed).toHaveAttribute('title', 'cmux could not make the terminal');
      expect(assign).not.toHaveBeenCalled();
    });

    it('a closed worktree has no cmux control', async () => {
      const { server } = await renderApp();
      clickNode('NORT-12');
      expect(within(expanded()).getByRole('link', { name: 'cmux' })).toBeInTheDocument();

      act(() => {
        server.change({ kind: 'worktree', ids: ['northwind-delta'] }, (w) => {
          w.worktrees['northwind-delta'] = { ...w.worktrees['northwind-delta']!, isClosed: true };
        });
      });

      await waitFor(() =>
        expect(within(expanded()).queryByRole('link', { name: 'cmux' })).toBeNull(),
      );
      expect(within(expanded()).queryByRole('button', { name: 'cmux' })).toBeNull();
    });

    it("syncs the card's worktree with a plain sync", async () => {
      const user = userEvent.setup();
      const { server } = await renderApp();
      clickNode('NORT-12');
      await user.click(within(expanded()).getByRole('button', { name: 'Sync' }));
      await waitFor(() =>
        expect(server.requests).toContainEqual(
          expect.objectContaining({
            method: 'POST',
            path: '/api/worktrees/northwind-delta/sync',
            body: { mode: 'plain' },
          }),
        ),
      );
    });

    it('offers Merge only once the PR is ready, and merges after the confirm', async () => {
      const user = userEvent.setup();
      const { server } = await renderApp();
      clickNode('NORT-12');
      // CI is still running, so there is nothing to merge yet.
      expect(within(expanded()).queryByRole('button', { name: 'Merge' })).toBeNull();
      makeDeltaReady(server);

      await user.click(await within(expanded()).findByRole('button', { name: 'Merge' }));
      // Asking is not merging: nothing is sent until the answer.
      expect(server.requests.some((r) => r.path.endsWith('/merge-pr'))).toBe(false);
      const ask = screen.getByRole('alertdialog', { name: 'Merge PR #118 into main?' });
      await user.click(within(ask).getByRole('button', { name: 'Merge' }));

      await waitFor(() =>
        expect(server.requests).toContainEqual(
          expect.objectContaining({
            method: 'POST',
            path: '/api/worktrees/northwind-delta/merge-pr',
          }),
        ),
      );
      // The PR is merged, so the button goes.
      await waitFor(() =>
        expect(within(expanded()).queryByRole('button', { name: 'Merge' })).toBeNull(),
      );
    });

    it('offers no Merge on a draft PR, whatever its checks say', async () => {
      const { server } = await renderApp();
      clickNode('NORT-12');
      makeDeltaReady(server, { prDraft: true });
      await within(expanded()).findByRole('link', { name: /PR #118, draft/ });
      expect(within(expanded()).queryByRole('button', { name: 'Merge' })).toBeNull();
    });

    it('offers Merge disabled while the local branch differs from the PR', async () => {
      const { server } = await renderApp();
      clickNode('NORT-12');
      makeDeltaReady(server, { prMatch: 'differ' });
      const merge = await within(expanded()).findByRole('button', { name: 'Merge' });
      expect(merge).toBeDisabled();
      expect(merge).toHaveAttribute(
        'title',
        'The local branch differs from the PR. Sync it first.',
      );
    });

    it('a refused merge says what GitHub said, and keeps the question open', async () => {
      const user = userEvent.setup();
      const { server } = await renderApp();
      server.refuse(/POST \/api\/worktrees\/[^/]+\/merge-pr$/, {
        status: 400,
        code: 'invalid',
        message: 'Head branch was modified. Review and try the merge again.',
      });
      clickNode('NORT-12');
      makeDeltaReady(server);

      await user.click(await within(expanded()).findByRole('button', { name: 'Merge' }));
      const ask = screen.getByRole('alertdialog', { name: 'Merge PR #118 into main?' });
      await user.click(within(ask).getByRole('button', { name: 'Merge' }));

      const failed = await within(ask).findByRole('button', { name: 'Failed' });
      expect(failed).toHaveAttribute(
        'title',
        'Head branch was modified. Review and try the merge again.',
      );
    });

    it('offers no env or sync control on a card with no worktree', async () => {
      await renderApp();
      // Blocked and not yet launched, so nothing has made its worktree.
      clickNode('NORT-15');
      expect(within(expanded()).queryByRole('button', { name: /env$/ })).toBeNull();
      expect(within(expanded()).queryByRole('button', { name: /^Sync/ })).toBeNull();
    });

    it('offers no env or sync control once the worktree is closed', async () => {
      const { server } = await renderApp();
      clickNode('NORT-12');
      expect(within(expanded()).getByRole('button', { name: 'Stop env' })).toBeInTheDocument();
      expect(within(expanded()).getByRole('button', { name: 'Sync' })).toBeInTheDocument();
      act(() => {
        server.change({ kind: 'worktree', ids: ['northwind-delta'] }, (w) => {
          w.worktrees['northwind-delta'] = { ...w.worktrees['northwind-delta']!, isClosed: true };
        });
      });
      await waitFor(() =>
        expect(within(expanded()).queryByRole('button', { name: /env$/ })).toBeNull(),
      );
      expect(within(expanded()).queryByRole('button', { name: /^Sync/ })).toBeNull();
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

  it('shows an image the brief attaches as a thumbnail the browser can fetch', async () => {
    const { server } = await renderApp();
    act(() => {
      server.change({ kind: 'task', ids: ['NORT-7.1'] }, (w) => {
        w.tasks['NORT-7.1'] = {
          ...w.tasks['NORT-7.1']!,
          content: 'See ![shot]({{MAEL_TASK_DIR}}/images/NORT-7.1/shot.png)',
        };
      });
    });
    clickNode('NORT-7.1');
    const brief = await within(expanded()).findByTestId('task-content');
    const thumb = within(brief).getByRole('button', { name: 'shot — open full size' });
    expect(within(thumb).getByRole('img')).toHaveAttribute(
      'src',
      '/api/attachments/northwind/NORT-7.1/shot.png',
    );
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

  it('a plan-mode build task draws plan until its plan is approved, then build', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    const phase = () =>
      document.querySelector('[data-task-id="NORT-7"]')?.getAttribute('data-phase');
    server.world.tasks['NORT-7'] = { ...server.world.tasks['NORT-7']!, command: '', mode: 'plan' };
    server.change({ kind: 'task', ids: ['NORT-7'] });
    await waitFor(() => expect(phase()).toBe('plan'));
    clickNode('NORT-7');
    await user.click(await within(expanded()).findByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(phase()).toBe('build'));
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
    // The Read call before that message: a decision shows what was said only.
    expect(card).not.toHaveTextContent('orchestrator-ui/PRODUCT.md');
    await user.click(within(prompt).getAllByRole('radio')[0]!);
    await user.click(within(prompt).getByRole('button', { name: 'Answer' }));
    await waitFor(() => expect(nodeState('MAEL-52')).not.toBe('needs-attention'));
  });

  it('Decline denies the question with the fixed reason and clears the attention', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    clickNode('MAEL-52');
    const prompt = await within(expanded()).findByTestId('question-prompt');
    await user.click(within(prompt).getByRole('button', { name: 'Decline' }));
    await waitFor(() => expect(nodeState('MAEL-52')).not.toBe('needs-attention'));
    const deny = server.requests.find((r) => r.path.endsWith('/deny'));
    // The one question now filed as declined is the one the deny named.
    const declined = Object.values(server.transcripts)
      .flatMap((t) => t.items)
      .filter((i) => i.type === 'question' && i.declined);
    expect(declined).toHaveLength(1);
    expect(deny?.body).toEqual({
      requestId: (declined[0] as { requestId: string }).requestId,
      reason: 'The user declined to answer.',
    });
    expect(server.requests.some((r) => r.path.endsWith('/interrupt'))).toBe(false);
  });

  it('Decline & stop interrupts the agent, and the attention clears', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    clickNode('MAEL-52');
    const prompt = await within(expanded()).findByTestId('question-prompt');
    await user.click(within(prompt).getByRole('button', { name: 'More ways to decline' }));
    await user.click(within(prompt).getByRole('menuitem', { name: 'Decline & stop' }));
    await waitFor(() => expect(nodeState('MAEL-52')).not.toBe('needs-attention'));
    expect(server.requests.some((r) => r.path.endsWith('/interrupt'))).toBe(true);
    expect(server.requests.some((r) => r.path.endsWith('/deny'))).toBe(false);
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
    // The control sits on the id line of the card.
    const idLine = () => within(within(expanded()).getByTestId('node-id-line'));

    await user.click(
      idLine().getByRole('button', { name: 'Status of Watch the migration PR, todo' }),
    );
    await user.selectOptions(idLine().getByRole('combobox'), 'blocked');

    expect(
      await idLine().findByRole('button', {
        name: 'Status of Watch the migration PR, blocked',
      }),
    ).toBeInTheDocument();
    expect(idLine().queryByRole('combobox')).toBeNull();
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

  it('opens the task editor from the card, the same dialog the task list opens', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('NORT-9');

    await user.click(within(expanded()).getByRole('button', { name: 'Edit task' }));

    const editor = await screen.findAllByRole('dialog', { name: 'Migrate to Postgres 16' });
    // The card is one dialog, the editor another; the editor's Title field is
    // what proves the second one opened.
    expect(editor).toHaveLength(2);
    const withTitle = editor.find((d) => within(d).queryByLabelText('Title'));
    expect(withTitle).toBeDefined();
    expect(within(withTitle!).getByLabelText('Title')).toHaveValue('Migrate to Postgres 16');
  });

  it('has no Edit task button on a free agent, which has no task to open', async () => {
    await renderApp();
    clickNode('f2c6a9d4');
    expect(within(expanded()).queryByRole('button', { name: 'Edit task' })).toBeNull();
  });
});

/** Put NORT-9's agent's last message `minutesAgo`, in whatever `state`. */
/** Finish CI on NORT-12's pull request, as the worktree poll would report it. */
function makeDeltaReady(server: FakeServer, over: Partial<Worktree> = {}) {
  act(() => {
    server.change({ kind: 'worktree', ids: ['northwind-delta'] }, (w) => {
      w.worktrees['northwind-delta'] = {
        ...w.worktrees['northwind-delta']!,
        prState: 'ready',
        ...over,
      };
    });
  });
}

function spokeAt(server: FakeServer, minutesAgo: number, state: Agent['state']) {
  server.change({ kind: 'agent', ids: ['d9a4c7f1'] }, (w) => {
    const agent = w.agents['d9a4c7f1']!;
    agent.lastMessageAt = new Date(Date.now() - minutesAgo * 60_000).toISOString();
    agent.state = state;
  });
}

describe('how long since the agent spoke', () => {
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

describe('what an unanswered agent last said', () => {
  it('leaves a partial message out of its recent messages', async () => {
    const { server } = await renderApp();
    const said = { ts: '', type: 'message' as const, role: 'assistant' as const };
    server.append('c3e8f1b5', { ...said, id: 'c3e8f1b5-whole', markdown: 'The tests pass.' });
    server.append('c3e8f1b5', {
      ...said,
      id: 'c3e8f1b5-partial',
      markdown: 'The restamp is half',
      partial: true,
    });
    act(() => endTurn(server));
    clickNode('MAEL-40.1');

    const list = await within(expanded()).findByTestId('recent-messages');
    expect(list).toHaveTextContent('The tests pass.');
    expect(list).not.toHaveTextContent('The restamp is half');

    // The same item, once the message is whole.
    act(() =>
      server.patch('c3e8f1b5', 'c3e8f1b5-partial', {
        markdown: 'The restamp is written.',
        partial: false,
      }),
    );
    await waitFor(() => expect(list).toHaveTextContent('The restamp is written.'));
  });

  it('shows its recent messages in place of the one-line Now block', async () => {
    const { server } = await renderApp();
    server.append('c3e8f1b5', {
      id: 'c3e8f1b5-asks',
      ts: '',
      type: 'message',
      role: 'assistant',
      markdown: 'The tests pass. Do you want the restamp on every HEAD move?',
    });
    act(() => endTurn(server));
    clickNode('MAEL-40.1');

    const list = await within(expanded()).findByTestId('recent-messages');
    // The earlier message is not the agent's `lastMessage`: only the
    // transcript holds it.
    expect(list).toHaveTextContent('Adding the HEAD staleness check to the index reader.');
    expect(list).toHaveTextContent('Do you want the restamp on every HEAD move?');
    const card = expanded();
    expect(card).toHaveTextContent('Unanswered');
    expect(card).toHaveTextContent('Last said');
    expect(within(card).getByTestId('now-age')).toBeInTheDocument();

    // A reply starts a turn, and the transcript is loaded by now: the list
    // goes because of the state, not because there is nothing to list.
    act(() => {
      server.change({ kind: 'agent', ids: ['c3e8f1b5'] }, (w) => {
        w.agents['c3e8f1b5'] = { ...w.agents['c3e8f1b5']!, state: 'processing' };
      });
    });
    await waitFor(() => expect(within(expanded()).queryByTestId('recent-messages')).toBeNull());
    expect(expanded()).toHaveTextContent('Now');
  });

  it('takes a reply on the card, with no session open', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    act(() => endTurn(server));
    clickNode('MAEL-40.1');

    const box = await within(expanded()).findByTestId('unanswered');
    const field = within(box).getByRole('textbox', { name: 'Reply to agent' });
    await user.type(field, 'Restamp on every move.');
    await user.click(within(box).getByRole('button', { name: 'Send' }));

    await waitFor(() =>
      expect(server.requests.find((r) => r.path === '/api/agents/c3e8f1b5/say')?.body).toEqual({
        text: 'Restamp on every move.',
      }),
    );
    expect(screen.queryByTestId('session-tab')).toBeNull();
    // The fake's `say` starts no turn, so the box is still here to read.
    await waitFor(() => expect(field).toHaveValue(''));
  });

  it('holds the reply apart from the message typed in the session tab', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    act(() => endTurn(server));
    clickNode('MAEL-40.1');
    await user.click(within(expanded()).getByRole('link', { name: 'Session' }));
    const message = screen.getByRole('textbox', { name: 'Message to agent' });
    await user.type(message, 'A draft.');
    // Past the write delay, so the draft is in storage when the card mounts.
    await new Promise((r) => setTimeout(r, 400));
    clickNode('MAEL-40.1');
    clickNode('MAEL-40.1');

    // Both are mounted for one agent. On one key the card would open with
    // the draft, and a send from it would leave the draft in the session tab.
    const box = await within(expanded()).findByTestId('unanswered');
    expect(within(box).getByRole('textbox', { name: 'Reply to agent' })).toHaveValue('');
    expect(message).toHaveValue('A draft.');
  });

  it('shows the last message in the box until the transcript arrives', async () => {
    const { server } = await renderApp({ transcripts: {} });
    act(() => endTurn(server));
    clickNode('MAEL-40.1');

    const box = await within(expanded()).findByTestId('unanswered');
    expect(within(box).getByTestId('recent-messages')).toHaveTextContent(
      'Adding the HEAD staleness check',
    );
    expect(within(box).getByRole('textbox', { name: 'Reply to agent' })).toBeInTheDocument();
  });
});

describe('what the agent says it is doing', () => {
  /** Give NORT-9's agent a note, written `minutesAgo`. */
  function noted(server: FakeServer, note: string, minutesAgo = 0) {
    server.change({ kind: 'agent', ids: ['d9a4c7f1'] }, (w) => {
      const agent = w.agents['d9a4c7f1']!;
      agent.lastNote = note;
      agent.lastNoteAt = new Date(Date.now() - minutesAgo * 60_000).toISOString();
    });
  }

  it('shows the note in place of the last message', async () => {
    const { server } = await renderApp();
    noted(server, 'Rebasing onto main, then re-running the port test');
    clickNode('NORT-9');
    await waitFor(() => {
      const card = expanded();
      expect(card).toHaveTextContent('Rebasing onto main, then re-running the port test');
      // The seed's last message for this agent, which the note displaces.
      expect(card).not.toHaveTextContent('Rewriting the migration for the new collation.');
    });
  });

  it('falls back to the last message once a note is cleared', async () => {
    // Asserting the seed would pass with the fallback deleted: its `lastNote`
    // is already empty. Setting a note and clearing it exercises the branch.
    const { server } = await renderApp();
    noted(server, 'Rebasing onto main');
    clickNode('NORT-9');
    await waitFor(() => expect(expanded()).toHaveTextContent('Rebasing onto main'));
    noted(server, '');
    await waitFor(() => {
      const card = expanded();
      expect(card).toHaveTextContent('Rewriting the migration for the new collation.');
      expect(card).not.toHaveTextContent('Rebasing onto main');
    });
  });

  it('still dates the block from the last message, not the note', async () => {
    // A note is not speech. Dating the block from the note would make an agent
    // that noted once look alive for ever, which is the failure this shows.
    const { server } = await renderApp();
    spokeAt(server, 40, 'processing');
    noted(server, 'Waiting on the test run', 1);
    clickNode('NORT-9');
    await waitFor(() =>
      expect(within(expanded()).getByTestId('now-age')).toHaveTextContent('40m ago'),
    );
  });

  it('still marks a working agent silent when only its note is recent', async () => {
    const { server } = await renderApp();
    spokeAt(server, 30, 'processing');
    noted(server, 'Still going', 0);
    clickNode('NORT-9');
    await waitFor(() =>
      expect(within(expanded()).getByTestId('now-age').closest('[data-silent]')).not.toBeNull(),
    );
  });

  it('marks the note as the agents own summary, apart from its last words', async () => {
    const { server } = await renderApp();
    noted(server, 'Reading the reducer');
    clickNode('NORT-9');
    await waitFor(() =>
      expect(
        within(expanded()).getByText('Reading the reducer').closest('[data-note]'),
      ).not.toBeNull(),
    );
  });
});

describe('the state in words', () => {
  it('the session tab names the wait when its agent is blocked on the user', async () => {
    const { server } = await renderApp();
    askQuestion(server);
    clickNode('NORT-9');
    await userEvent.setup().click(await within(expanded()).findByRole('link', { name: 'Session' }));
    await waitFor(() => expect(tabBody()).toHaveTextContent('Needs you · question'));
  });

  it('the session tab declines its question, and the transcript then reads Declined', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    askQuestion(server);
    clickNode('NORT-9');
    await user.click(await within(expanded()).findByRole('link', { name: 'Session' }));
    // The tab owns the prompt only while no node is expanded on its agent.
    pressKey('Escape');
    await user.click(await within(tabBody()).findByRole('button', { name: 'Decline' }));
    expect(await within(tabBody()).findByTestId('question-declined')).toHaveTextContent('Declined');
    expect(server.requests.find((r) => r.path.endsWith('/deny'))?.body).toEqual({
      requestId: 'req-nort9-q',
      reason: 'The user declined to answer.',
    });
  });

  it('Decline & stop from the session tab settles the question as interrupted', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp();
    askQuestion(server);
    clickNode('NORT-9');
    await user.click(await within(expanded()).findByRole('link', { name: 'Session' }));
    pressKey('Escape');
    await user.click(
      await within(tabBody()).findByRole('button', { name: 'More ways to decline' }),
    );
    await user.click(within(tabBody()).getByRole('menuitem', { name: 'Decline & stop' }));
    expect(await within(tabBody()).findByTestId('question-declined')).toHaveTextContent(
      'Declined · Interrupted by user',
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
    // Drift is a channel, not a state of its own: the Two Calls Rule
    // keeps the border and glow for work that calls the user.
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

  describe('the milestone band', () => {
    it('says the stage last reached, what it cost and how long ago', async () => {
      // The latest only, not a six-stage rail: the card is already carrying
      // the brief and the decision. The figures are the stage's own delta.
      const { server } = await renderApp();
      // The seed's stamps are relative to a fixed past date, so the age would
      // drift with the wall clock. Date this one from now, as `spokeAt` does.
      const closedAt = new Date(Date.now() - 6 * 60_000).toISOString();
      server.world.milestones.c3e8f1b5!.at(-1)!.at = closedAt;
      clickNode('MAEL-40.1');
      const band = await within(expanded()).findByTestId('milestone-band');
      expect(band).toHaveTextContent('built · 19k · $0.24 · 6m ago');
      expect(band.querySelector('time')).toHaveAttribute('datetime', closedAt);
    });

    it('follows a stage the agent reaches while the card is open', async () => {
      // The ledger is the source of record, but nothing about it moves the
      // world, so no change notice fires. The bar arriving on the transcript
      // is the signal that there is a newer stage to read.
      const { server } = await renderApp();
      clickNode('MAEL-40.1');
      expect(await within(expanded()).findByTestId('milestone-band')).toHaveTextContent('built');

      act(() => {
        server.world.milestones.c3e8f1b5!.push({
          name: 'reviewed',
          at: T(0),
          recognised: true,
          total_tokens: 48_000,
          delta_tokens: 16_100,
          own_delta: 16_100,
          subagent_delta: 0,
          cost_delta: 0.19,
        });
        server.append('c3e8f1b5' as Agent['id'], {
          id: 'item-reviewed' as TranscriptItem['id'],
          ts: T(0),
          type: 'milestone',
          name: 'reviewed',
          recognised: true,
          deltaTokens: 16_100,
          costDelta: 0.19,
        });
      });

      await waitFor(() =>
        expect(within(expanded()).getByTestId('milestone-band')).toHaveTextContent('reviewed'),
      );
    });

    it('says only the stage and its age when the stage spent nothing', async () => {
      // A stage reached twice deltas to zero, which is a real ledger state.
      const { server } = await renderApp();
      server.world.milestones.c3e8f1b5!.push({
        name: 'built',
        at: new Date(Date.now() - 3 * 60_000).toISOString(),
        recognised: true,
        total_tokens: 31_900,
        delta_tokens: 0,
        own_delta: 0,
        subagent_delta: 0,
        cost_delta: 0,
      });
      clickNode('MAEL-40.1');
      const band = await within(expanded()).findByTestId('milestone-band');
      expect(band).toHaveTextContent('built · 3m ago');
    });

    it('flags a stage name the flow does not declare', async () => {
      // The card shows the latest stage only, so a typo on the last marker
      // would otherwise hide the real progress behind a fiction.
      const { server } = await renderApp();
      server.world.milestones.c3e8f1b5!.push({
        name: 'deploed',
        at: T(2),
        recognised: false,
        total_tokens: 40_000,
        delta_tokens: 8_100,
        own_delta: 8_100,
        subagent_delta: 0,
        cost_delta: 0.09,
      });
      clickNode('MAEL-40.1');
      const band = await within(expanded()).findByTestId('milestone-band');
      expect(band).toHaveTextContent('deploed (?)');
      expect(band).toHaveAttribute('data-recognised', 'false');
    });

    it('draws nothing at all for an agent that has reached no stage', async () => {
      // Most agents have none, and an empty band would cost every card a row.
      await renderApp();
      clickNode('NORT-7');
      await waitFor(() => expect(within(expanded()).getByTestId('node-meta')).toBeInTheDocument());
      expect(within(expanded()).queryByTestId('milestone-band')).toBeNull();
    });
  });
});

describe('follows relations on the expanded node', () => {
  const group = (name: string) =>
    within(within(expanded()).getByTestId('node-follows')).getByRole('group', { name });
  /** Each listed row as its id, status and desk state. */
  const listed = (name: string) =>
    Array.from(group(name).querySelectorAll<HTMLElement>('[data-on-desk]')).map((r) => ({
      id: within(r).getByTestId('follows-id').textContent,
      status: within(r).getByTestId('follows-status').textContent,
      onDesk: r.dataset.onDesk === 'true',
    }));
  const row = (name: string, id: string) =>
    within(group(name)).getByText(id).closest('[data-on-desk]') as HTMLElement;

  it('lists what the task follows and what follows it, the indirect ones too', async () => {
    await renderApp();
    clickNode('MAEL-40.1');
    expect(within(group('Follows')).getByText('Task index cache')).toBeInTheDocument();
    // The fixture leaves MAEL-40 off the desk.
    expect(listed('Follows')).toEqual([{ id: 'MAEL-40', status: 'done', onDesk: false }]);
    expect(listed('Followed by')).toEqual([{ id: 'MAEL-40.2', status: 'todo', onDesk: true }]);
    clickNode('MAEL-52');
    expect(within(expanded()).queryByRole('group', { name: 'Follows' })).toBeNull();
    expect(listed('Followed by')).toEqual([
      { id: 'MAEL-52.1', status: 'todo', onDesk: true },
      { id: 'MAEL-52.2', status: 'todo', onDesk: true },
    ]);
  });

  it('puts a task off the desk on it, and the canvas draws its node', async () => {
    const user = userEvent.setup();
    await renderApp();
    expect(document.querySelector('[data-task-id="MAEL-40"]')).toBeNull();
    clickNode('MAEL-40.1');
    await user.click(within(row('Follows', 'MAEL-40')).getByRole('button', { name: 'On desk' }));
    await waitFor(() =>
      expect(document.querySelector('[data-task-id="MAEL-40"]')).toBeInTheDocument(),
    );
    expect(
      within(row('Follows', 'MAEL-40')).getByRole('button', { name: 'Off desk' }),
    ).toBeInTheDocument();
  });

  it('takes a listed task off the desk', async () => {
    const user = userEvent.setup();
    await renderApp();
    clickNode('MAEL-40.1');
    await user.click(
      within(row('Followed by', 'MAEL-40.2')).getByRole('button', { name: 'Off desk' }),
    );
    await waitFor(() =>
      expect(document.querySelector('[data-task-id="MAEL-40.2"]')).not.toBeInTheDocument(),
    );
  });

  it('draws no section for a task with no follows relations', async () => {
    const { server } = await renderApp();
    clickNode('NORT-9');
    expect(within(expanded()).getByTestId('node-follows')).toBeInTheDocument();
    act(() => {
      server.change({ kind: 'task', ids: ['NORT-9.1'] }, (w) => {
        w.tasks['NORT-9.1'] = { ...w.tasks['NORT-9.1']!, follows: [] };
      });
    });
    await waitFor(() => expect(within(expanded()).queryByTestId('node-follows')).toBeNull());
  });
});
