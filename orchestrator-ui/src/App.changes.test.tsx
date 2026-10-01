import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import { act } from 'react';
import userEvent from '@testing-library/user-event';
import type { FileDiff } from './protocol/entities';
import { expanded } from './test/appHelpers';
import type { FakeServer } from './test/fakeServer';
import { clickNode, renderApp } from './test/renderApp';

/** NORT-12's agent runs in delta, so its expanded node reaches delta's changes. */
const DELTA = 'northwind-delta';
const SHA = 'c0ffee1234567890c0ffee1234567890c0ffee12';
const LATER_SHA = 'decade1234567890decade1234567890decade12';

const file = (path: string, text: string, over: Partial<FileDiff> = {}): FileDiff => ({
  path,
  oldPath: null,
  status: 'modified',
  binary: false,
  additions: 1,
  deletions: 1,
  truncated: false,
  hunks: [
    {
      header: '@@ -4,2 +4,2 @@',
      lines: [
        { kind: 'context', text: 'import os', oldLine: 4, newLine: 4 },
        { kind: 'remove', text: `old ${text}`, oldLine: 5, newLine: null },
        { kind: 'add', text: `new ${text}`, oldLine: null, newLine: 5 },
      ],
    },
  ],
  ...over,
});

function seedChanges(server: FakeServer) {
  server.world.changes[DELTA] = {
    changes: {
      dirtyFiles: [
        { path: 'auth/tokens.py', status: 'M' },
        { path: 'auth/rotate.py', status: '?' },
      ],
      base: 'main',
      commits: [
        {
          sha: SHA,
          shortSha: 'c0ffee1',
          subject: 'feat: rotate on expiry',
          body: 'A token past its expiry\nnow rotates, so `refresh()` runs.\n\nThe old one stays:\n- for a minute\n- once',
          author: 'Sam',
          date: '2026-09-02T09:00:00+12:00',
          filesChanged: 1,
        },
        {
          sha: LATER_SHA,
          shortSha: 'decade1',
          subject: 'fix: keep the old token a minute',
          body: '',
          author: 'Sam',
          date: '2026-09-02T10:00:00+12:00',
          filesChanged: 1,
        },
      ],
    },
    diffs: {
      uncommitted: [
        file('auth/tokens.py', 'tokens'),
        file('auth/rotate.py', 'rotate', { status: 'added' }),
      ],
      branch: [file('auth/expiry.py', 'expiry'), file('auth/grace.py', 'grace')],
      [SHA]: [file('auth/expiry.py', 'expiry')],
      [LATER_SHA]: [file('auth/grace.py', 'grace')],
    },
  };
}

type Changes = FakeServer['world']['changes'][string];

/** Open delta's Changes tab from NORT-12's node. `tweak` edits the seeded changes first. */
async function openChanges(tweak?: (changes: Changes) => void) {
  const { server } = await renderApp();
  seedChanges(server);
  tweak?.(server.world.changes[DELTA]!);
  clickNode('NORT-12');
  await userEvent.click(within(expanded()).getByRole('link', { name: 'Changes' }));
  const panel = screen.getByTestId('panel');
  if (!tweak) await waitFor(() => expect(rowTexts(panel, 'auth/tokens.py')).toHaveLength(3));
  return { server, panel };
}

/** The text of each diff row in a file's card, token spans and all. */
const rowTexts = (panel: HTMLElement, path: string) =>
  within(within(panel).getByRole('region', { name: path }))
    .getAllByTestId('diff-row')
    .map((row) => row.textContent);

/** Every row of the file's card, once its tokens are drawn. */
async function highlighted(panel: HTMLElement, path: string) {
  const rows = () =>
    within(within(panel).getByRole('region', { name: path })).getAllByTestId('diff-row');
  await waitFor(() => rows().forEach((row) => expect(row).toHaveAttribute('data-highlighted')));
  return rows();
}

/** The strip of revs beside the diff. */
const strip = () => screen.getByRole('navigation', { name: 'Changes to show' });
const entries = () =>
  within(strip())
    .getAllByRole('button')
    .map((b) => b.textContent);
const current = () => within(strip()).getByRole('button', { current: true }).textContent;
const pick = (name: RegExp) => userEvent.click(within(strip()).getByRole('button', { name }));

describe('the Changes tab', () => {
  const scrolled = vi.fn();
  beforeEach(() => {
    Element.prototype.scrollIntoView = scrolled;
  });
  afterEach(() => {
    scrolled.mockReset();
  });

  it('opens from the expanded node on the uncommitted changes, named by its worktree id', async () => {
    const { panel } = await openChanges();
    expect(screen.getByRole('tab', { name: 'northwind-delta changes' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(current()).toBe('Uncommitted 2');
    const files = within(panel).getByRole('list', { name: 'Files' });
    expect(
      within(files)
        .getAllByRole('listitem')
        .map((li) => li.textContent),
    ).toEqual([
      expect.stringContaining('auth/tokens.py'),
      expect.stringContaining('auth/rotate.py'),
    ]);
    // Old and new line numbers sit beside each row.
    const rows = within(
      within(panel).getByRole('region', { name: 'auth/tokens.py' }),
    ).getAllByTestId('diff-row');
    expect(rows[1]).toHaveAttribute('data-kind', 'remove');
    expect(rows[1]).toHaveTextContent('5-old tokens');
  });

  it('lists the uncommitted changes, the whole branch and each commit in a strip', async () => {
    const { server, panel } = await openChanges();
    // The commits keep the order the server sent: oldest first.
    expect(entries()).toEqual([
      'Uncommitted 2',
      'All commits 2',
      'c0ffee1 feat: rotate on expiry',
      'decade1 fix: keep the old token a minute',
    ]);

    await pick(/rotate on expiry/);
    await waitFor(() => expect(rowTexts(panel, 'auth/expiry.py')).toHaveLength(3));
    expect(within(panel).queryByRole('region', { name: 'auth/tokens.py' })).toBeNull();
    expect(current()).toBe('c0ffee1 feat: rotate on expiry');
    expect(server.requests.map((r) => r.path)).toContain(`/api/worktrees/${DELTA}/diff?rev=${SHA}`);
  });

  it("shows a commit's message above its diff, and folds it away", async () => {
    const { panel } = await openChanges();
    expect(within(panel).queryByRole('article', { name: 'Commit message' })).toBeNull();

    await pick(/rotate on expiry/);
    const message = await within(panel).findByRole('article', { name: 'Commit message' });
    // The body is Markdown: git's hard wraps join, and lists and code spans draw.
    const body = within(message).getByText(/A token past its expiry now rotates/);
    expect(body).toBeVisible();
    expect(within(message).getByText('refresh()').tagName).toBe('CODE');
    expect(
      within(message)
        .getAllByRole('listitem')
        .map((li) => li.textContent),
    ).toEqual(['for a minute', 'once']);

    // The subject is the fold button, on a line that stays above the message.
    const fold = within(panel).getByRole('button', { name: 'feat: rotate on expiry' });
    expect(fold).toHaveAttribute('aria-expanded', 'true');
    await userEvent.click(fold);
    expect(fold).toHaveAttribute('aria-expanded', 'false');
    expect(body).not.toBeVisible();

    // The message stays folded on the next commit.
    await userEvent.click(within(panel).getByRole('button', { name: 'Next commit' }));
    await waitFor(() => expect(rowTexts(panel, 'auth/grace.py')).toHaveLength(3));
    expect(
      within(panel).getByRole('button', { name: 'fix: keep the old token a minute' }),
    ).toHaveAttribute('aria-expanded', 'false');
  });

  it('folds the file list away under its totals', async () => {
    const { panel } = await openChanges();
    const fold = within(panel).getByRole('button', { name: /^2 files/ });
    expect(fold).toHaveTextContent('2 files +2 −2');
    const files = within(panel).getByRole('list', { name: 'Files' });
    expect(files).toBeVisible();

    await userEvent.click(fold);
    expect(fold).toHaveAttribute('aria-expanded', 'false');
    expect(files).not.toBeVisible();
    expect(within(panel).getByRole('region', { name: 'auth/tokens.py' })).toBeVisible();

    // The list stays folded on another rev.
    await pick(/rotate on expiry/);
    expect(await within(panel).findByRole('button', { name: /^1 file/ })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
  });

  it('steps through the commits with Previous and Next, and stops at each end', async () => {
    const { server, panel } = await openChanges();
    // Uncommitted and All commits are not steps.
    expect(within(panel).queryByRole('button', { name: 'Next commit' })).toBeNull();
    await pick(/All commits/);
    await waitFor(() => expect(rowTexts(panel, 'auth/grace.py')).toHaveLength(3));
    expect(within(panel).queryByRole('button', { name: 'Previous commit' })).toBeNull();

    await pick(/rotate on expiry/);
    const prev = () => within(panel).getByRole('button', { name: 'Previous commit' });
    const next = () => within(panel).getByRole('button', { name: 'Next commit' });
    await waitFor(() => expect(prev()).toBeDisabled());
    expect(next()).toBeEnabled();

    await userEvent.click(next());
    await waitFor(() => expect(rowTexts(panel, 'auth/grace.py')).toHaveLength(3));
    expect(current()).toBe('decade1 fix: keep the old token a minute');
    expect(server.requests.map((r) => r.path)).toContain(
      `/api/worktrees/${DELTA}/diff?rev=${LATER_SHA}`,
    );
    expect(next()).toBeDisabled();

    // A commit seen before opens at the top, not where the last one was scrolled to.
    const scroll = (path: string) =>
      within(panel).getByRole('region', { name: path }).parentElement!;
    scroll('auth/grace.py').scrollTop = 300;
    await userEvent.click(prev());
    await waitFor(() => expect(current()).toBe('c0ffee1 feat: rotate on expiry'));
    expect(scroll('auth/expiry.py').scrollTop).toBe(0);
  });

  it('draws the changed files as a tree in the strip, which folds and jumps', async () => {
    const { panel } = await openChanges((c) => {
      c.diffs.uncommitted = [
        file('docs/specs/agent/stream.md', 'stream'),
        file('docs/specs/agent/reply.md', 'reply', { status: 'added' }),
        file('auth/tokens.py', 'tokens'),
        file('README.md', 'readme'),
      ];
    });
    const tree = await within(strip()).findByRole('tree', { name: 'Changed files' });
    const items = () =>
      within(tree)
        .getAllByRole('treeitem')
        .map((item) => item.textContent);
    // The order and the joined chain are `fileTree`'s rules; see tree.test.ts.
    expect(items()).toEqual([
      'auth',
      'Mtokens.py',
      'docs/specs/agent',
      'Areply.md',
      'Mstream.md',
      'MREADME.md',
    ]);

    const dir = within(tree).getByRole('treeitem', { name: 'docs/specs/agent' });
    expect(dir).toHaveAttribute('aria-expanded', 'true');
    await userEvent.click(dir);
    expect(dir).toHaveAttribute('aria-expanded', 'false');
    expect(items()).toEqual(['auth', 'Mtokens.py', 'docs/specs/agent', 'MREADME.md']);

    await userEvent.click(within(tree).getByRole('treeitem', { name: /tokens\.py/ }));
    expect(scrolled).toHaveBeenCalledTimes(1);
    expect(scrolled.mock.contexts[0]).toBe(
      within(panel).getByRole('region', { name: 'auth/tokens.py' }),
    );
  });

  it('scrolls to a file when its entry in the file list is clicked', async () => {
    const { panel } = await openChanges();
    const files = within(panel).getByRole('list', { name: 'Files' });
    await userEvent.click(within(files).getByRole('button', { name: /auth\/rotate\.py/ }));
    const block = within(panel).getByRole('region', { name: 'auth/rotate.py' });
    expect(scrolled).toHaveBeenCalledTimes(1);
    expect(scrolled.mock.contexts[0]).toBe(block);
  });

  it('reads the changes again when the worktree changes', async () => {
    const { server, panel } = await openChanges();
    await highlighted(panel, 'auth/tokens.py');
    act(() => {
      server.change({ kind: 'worktree', ids: [DELTA] }, (w) => {
        w.changes[DELTA]!.diffs.uncommitted = [file('auth/tokens.py', 'again')];
      });
    });
    await waitFor(() => expect(rowTexts(panel, 'auth/tokens.py')[2]).toContain('new again'));
    await highlighted(panel, 'auth/tokens.py');
    expect(rowTexts(panel, 'auth/tokens.py')).toEqual([
      '44 import os',
      '5-old again',
      '5+new again',
    ]);
  });

  it.each([
    {
      picked: /rotate on expiry/,
      why: 'a rebase rewrote the commit',
      change: (c: Changes) => (c.changes.commits = []),
      then: 'Uncommitted 2',
    },
    {
      picked: /All commits/,
      why: 'a reset left no commits',
      change: (c: Changes) => (c.changes.commits = []),
      then: 'Uncommitted 2',
    },
    {
      picked: /Uncommitted/,
      why: 'the agent committed everything',
      change: (c: Changes) => (c.changes.dirtyFiles = []),
      then: 'All commits 2',
    },
  ])('falls back to the default rev when $why', async ({ picked, change, then }) => {
    const { server } = await openChanges();
    await pick(picked);
    act(() => {
      server.change({ kind: 'worktree', ids: [DELTA] }, (w) => change(w.changes[DELTA]!));
    });
    // The picked entry is no longer drawn, so the default is current.
    await waitFor(() => expect(current()).toBe(then));
  });

  it('reads the changes again on Refresh, with no notice', async () => {
    const { server, panel } = await openChanges();
    server.world.changes[DELTA]!.diffs.uncommitted = [file('auth/tokens.py', 'refreshed')];
    await userEvent.click(within(panel).getByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(rowTexts(panel, 'auth/tokens.py')[2]).toContain('new refreshed'));
  });

  it('says a binary or a cut file is not shown whole, and names both paths of a rename', async () => {
    const { panel } = await openChanges((c) => {
      c.diffs.uncommitted = [
        file('logo.png', '', { status: 'added', binary: true, hunks: [] }),
        file('pnpm-lock.yaml', 'lock', { truncated: true }),
        file('auth/keys.py', 'keys', { status: 'renamed', oldPath: 'auth/secrets.py' }),
      ];
    });
    const region = (name: string) => within(panel).getByRole('region', { name });
    expect(await within(panel).findByText('Binary file, not shown.')).toBeInTheDocument();
    expect(region('logo.png')).toHaveTextContent('Binary file, not shown.');
    expect(region('pnpm-lock.yaml')).toHaveTextContent('The rest is cut.');
    expect(region('auth/keys.py')).toHaveTextContent('auth/secrets.py → auth/keys.py');
  });

  it('draws a known language in syntax colour, and an unknown one or a long line plain', async () => {
    const { panel } = await openChanges((c) => {
      c.diffs.uncommitted = [
        file('src/rotate.ts', 'const ttl = 60; // seconds'),
        file('notes/rotate.xyz', 'plain'),
        file('src/min.ts', 'x'.repeat(1001)),
      ];
    });
    const rows = await highlighted(panel, 'src/rotate.ts');
    // The tokens draw the same text the plain rows did.
    expect(rowTexts(panel, 'src/rotate.ts')).toEqual([
      '44 import os',
      '5-old const ttl = 60; // seconds',
      '5+new const ttl = 60; // seconds',
    ]);
    const comment = within(rows[2]!).getByText('// seconds', { exact: false });
    expect(comment.style.color).toBe('var(--syntax-token-comment)');
    for (const path of ['notes/rotate.xyz', 'src/min.ts'])
      within(within(panel).getByRole('region', { name: path }))
        .getAllByTestId('diff-row')
        .forEach((row) => expect(row).not.toHaveAttribute('data-highlighted'));
  });

  it('highlights each side of a hunk alone, so a comment one side opens does not colour the other', async () => {
    const { panel } = await openChanges((c) => {
      c.diffs.uncommitted = [
        file('src/rotate.ts', '', {
          hunks: [
            {
              header: '@@ -1,3 +1,3 @@',
              lines: [
                { kind: 'context', text: 'a();', oldLine: 1, newLine: 1 },
                { kind: 'remove', text: '/* old', oldLine: 2, newLine: null },
                { kind: 'add', text: 'const x = 1;', oldLine: null, newLine: 2 },
                { kind: 'context', text: 'y();', oldLine: 3, newLine: 3 },
              ],
            },
          ],
        }),
      ];
    });
    const rows = await highlighted(panel, 'src/rotate.ts');
    const firstColour = (row: HTMLElement) =>
      row.querySelector<HTMLElement>('[style]')?.style.color;
    expect(firstColour(rows[1]!)).toBe('var(--syntax-token-comment)');
    expect(firstColour(rows[3]!)).not.toBe('var(--syntax-token-comment)');
  });

  it('opens on all commits when nothing is uncommitted', async () => {
    const { panel } = await openChanges((c) => {
      c.changes.dirtyFiles = [];
    });
    await within(panel).findByText('new expiry');
    expect(entries()).toEqual([
      'All commits 2',
      'c0ffee1 feat: rotate on expiry',
      'decade1 fix: keep the old token a minute',
    ]);
    expect(current()).toBe('All commits 2');
  });

  it('draws no strip, and says so, when there are no changes', async () => {
    const { panel } = await openChanges((c) => {
      c.changes.dirtyFiles = [];
      c.changes.commits = [];
      c.diffs.branch = [];
    });
    expect(await within(panel).findByText('No commits ahead of main')).toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: 'Changes to show' })).toBeNull();
  });

  it('pushes a screen in the narrow layout', async () => {
    const { server } = await renderApp({ viewport: 'narrow' });
    seedChanges(server);
    await userEvent.click(screen.getByRole('button', { name: /Rotate auth tokens/ }));
    await userEvent.click(screen.getByRole('link', { name: 'Changes' }));
    expect(await screen.findByText('new tokens')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Back' })).toBeInTheDocument();
  });
});
