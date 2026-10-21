import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import { act } from 'react';
import userEvent from '@testing-library/user-event';
import type { FileDiff } from './protocol/entities';
import { expanded, openSheet, screenStrip } from './test/appHelpers';
import type { FakeServer } from './fake/fakeServer';
import { clickNode, renderApp } from './test/renderApp';
import type * as DiffSign from './ui/diffSign';

/** NORT-12's agent runs in delta, so its expanded node reaches delta's changes. */
const DELTA = 'northwind-delta';

/**
 * How many times `SIGN` was read. `DiffRow` reads it once per render, so one
 * read is one draw. `placeOf` and `spanOf` read it once per row they scan, so
 * the count also sees a search for a comment's place. Production code has no
 * hook that counts a render.
 */
const draws = vi.hoisted(() => ({ count: 0 }));
vi.mock('./ui/diffSign', async (importOriginal) => {
  const real = await importOriginal<typeof DiffSign>();
  const SIGN = new Proxy(real.SIGN, {
    get(target, key, receiver) {
      draws.count += 1;
      return Reflect.get(target, key, receiver) as unknown;
    },
  });
  return { ...real, SIGN };
});

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

/**
 * Open delta's Changes tab from NORT-12's node, on the uncommitted changes when
 * there are some. `tweak` edits the seeded changes first. `rev: null` leaves
 * the tab on the rev it opens on.
 */
async function openChanges(
  tweak?: (changes: Changes) => void,
  rev: RegExp | null = /^Uncommitted/,
) {
  const { server } = await renderApp();
  seedChanges(server);
  const changes = server.world.changes[DELTA]!;
  tweak?.(changes);
  clickNode('NORT-12');
  await userEvent.click(within(expanded()).getByRole('link', { name: 'Changes' }));
  const panel = screen.getByTestId('panel');
  if (rev && changes.changes.dirtyFiles.length > 0) {
    const nav = await screen.findByRole('navigation', { name: 'Changes to show' });
    await userEvent.click(within(nav).getByRole('button', { name: rev }));
  }
  if (!tweak && rev) await waitFor(() => expect(rowTexts(panel, 'auth/tokens.py')).toHaveLength(3));
  return { server, panel };
}

/** Open the tab and leave it on the rev it opens on. */
const openAsOpened = (tweak?: (changes: Changes) => void) => openChanges(tweak, null);

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

  it('opens from the expanded node, named by its worktree id', async () => {
    const { panel } = await openChanges();
    expect(screen.getByRole('tab', { name: 'northwind-delta changes' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
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

  it('lists each commit, then the whole branch and the uncommitted changes, and opens on the first commit', async () => {
    const { server, panel } = await openAsOpened();
    // The commits keep the order the server sent: oldest first.
    expect(entries()).toEqual([
      'c0ffee1 feat: rotate on expiry',
      'decade1 fix: keep the old token a minute',
      'All commits 2',
      'Uncommitted 2',
    ]);

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
    // Every directory starts shut.
    expect(items()).toEqual(['auth', 'docs/specs/agent', 'MREADME.md']);

    const dir = within(tree).getByRole('treeitem', { name: 'docs/specs/agent' });
    expect(dir).toHaveAttribute('aria-expanded', 'false');
    await userEvent.click(dir);
    expect(dir).toHaveAttribute('aria-expanded', 'true');
    expect(items()).toEqual(['auth', 'docs/specs/agent', 'Areply.md', 'Mstream.md', 'MREADME.md']);
    await userEvent.click(dir);
    expect(items()).toEqual(['auth', 'docs/specs/agent', 'MREADME.md']);

    await userEvent.click(within(tree).getByRole('treeitem', { name: 'auth' }));
    await userEvent.click(within(tree).getByRole('treeitem', { name: /tokens\.py/ }));
    expect(scrolled).toHaveBeenCalledTimes(1);
    expect(scrolled.mock.contexts[0]).toBe(
      within(panel).getByRole('region', { name: 'auth/tokens.py' }),
    );
  });

  it('scrolls to a file when its entry in the file list is clicked', async () => {
    const { panel } = await openChanges();
    const files = within(panel).getByRole('list', { name: 'Files' });
    await userEvent.click(within(files).getByRole('button', { name: /^A ?auth\/rotate\.py/ }));
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
      strip: ['Uncommitted 2'],
    },
    {
      picked: /All commits/,
      why: 'a reset left no commits',
      change: (c: Changes) => (c.changes.commits = []),
      then: 'Uncommitted 2',
      strip: ['Uncommitted 2'],
    },
    {
      picked: /Uncommitted/,
      why: 'the agent committed everything',
      change: (c: Changes) => (c.changes.dirtyFiles = []),
      then: 'c0ffee1 feat: rotate on expiry',
      strip: [
        'c0ffee1 feat: rotate on expiry',
        'decade1 fix: keep the old token a minute',
        'All commits 2',
      ],
    },
  ])('falls back to the default rev when $why', async ({ picked, change, then, strip: left }) => {
    const { server } = await openChanges();
    await pick(picked);
    act(() => {
      server.change({ kind: 'worktree', ids: [DELTA] }, (w) => change(w.changes[DELTA]!));
    });
    // The picked entry is no longer drawn, so the default is current.
    await waitFor(() => expect(current()).toBe(then));
    expect(entries()).toEqual(left);
  });

  it('reads the changes again on Refresh, with no notice', async () => {
    const { server, panel } = await openChanges();
    server.world.changes[DELTA]!.diffs.uncommitted = [file('auth/tokens.py', 'refreshed')];
    await userEvent.click(within(panel).getByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(rowTexts(panel, 'auth/tokens.py')[2]).toContain('new refreshed'));
  });

  it('says the PR matches when the local branch is the PR head', async () => {
    const { panel } = await openChanges();
    const match = within(panel).getByRole('group', { name: 'PR match' });
    expect(match).toHaveTextContent('PR #118 matches');
    expect(within(match).queryByRole('button', { name: 'Sync' })).toBeNull();
  });

  it('says the PR differs from the local branch, and syncs it from the header', async () => {
    const { server, panel } = await openChanges();
    act(() => {
      server.change({ kind: 'worktree', ids: [DELTA] }, (w) => {
        w.worktrees[DELTA] = { ...w.worktrees[DELTA]!, prMatch: 'differ' };
      });
    });
    const match = within(panel).getByRole('group', { name: 'PR match' });
    await waitFor(() => expect(match).toHaveTextContent('PR #118 differs'));

    const reads = () =>
      server.requests.filter((r) => r.path === `/api/worktrees/${DELTA}/changes`).length;
    const before = reads();
    await userEvent.click(within(match).getByRole('button', { name: 'Sync' }));
    await waitFor(() =>
      expect(server.requests).toContainEqual(
        expect.objectContaining({
          method: 'POST',
          path: `/api/worktrees/${DELTA}/sync`,
          body: { mode: 'plain' },
        }),
      ),
    );
    await waitFor(() => expect(match).toHaveTextContent('PR #118 matches'));
    expect(within(match).queryByRole('button', { name: 'Sync' })).toBeNull();
    // The sync moved the branch, so the Changes are read again.
    await waitFor(() => expect(reads()).toBeGreaterThan(before));
  });

  it('draws no PR match when the PR has no verdict', async () => {
    const { server, panel } = await openChanges();
    act(() => {
      server.change({ kind: 'worktree', ids: [DELTA] }, (w) => {
        w.worktrees[DELTA] = { ...w.worktrees[DELTA]!, prMatch: '' };
      });
    });
    await waitFor(() =>
      expect(within(panel).queryByRole('group', { name: 'PR match' })).toBeNull(),
    );
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
    // A binary file has nothing to render; every other file has a View.
    expect(within(region('logo.png')).queryByRole('button', { name: /^View/ })).toBeNull();
    for (const path of ['pnpm-lock.yaml', 'auth/keys.py'])
      expect(within(region(path)).getByRole('button', { name: `View ${path}` })).toBeVisible();
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

  it('opens on the uncommitted changes when the branch has no commits', async () => {
    const { panel } = await openAsOpened((c) => {
      c.changes.commits = [];
    });
    await within(panel).findByText('new tokens');
    expect(entries()).toEqual(['Uncommitted 2']);
    expect(current()).toBe('Uncommitted 2');
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

  /** Push delta's Changes screen in the narrow layout, on its first commit. */
  async function pushChanges(user: ReturnType<typeof userEvent.setup>) {
    const { server } = await renderApp({ viewport: 'narrow' });
    seedChanges(server);
    await user.click(screen.getByRole('link', { name: /Rotate auth tokens/ }));
    await user.click(screen.getByRole('link', { name: 'Changes' }));
    expect(await screen.findByText('new expiry')).toBeInTheDocument();
  }

  it('pushes a screen in the narrow layout that names the rev in the strip and steps through it', async () => {
    const user = userEvent.setup();
    await pushChanges(user);
    const bar = screenStrip();
    const tab = screen.getByTestId('changes-tab');
    expect(bar.getByTestId('screen-title')).toHaveTextContent('feat: rotate on expiry');
    // The body is the diff: no header, no rev list, no title line.
    expect(within(tab).queryByRole('button', { name: 'Refresh' })).toBeNull();
    expect(within(tab).queryByRole('navigation', { name: 'Changes to show' })).toBeNull();
    expect(within(tab).queryByRole('button', { name: 'Next commit' })).toBeNull();

    expect(bar.getByRole('button', { name: 'Previous commit' })).toBeDisabled();
    await user.click(bar.getByRole('button', { name: 'Next commit' }));
    expect(bar.getByTestId('screen-title')).toHaveTextContent('fix: keep the old token a minute');
    expect(await screen.findByText('new grace')).toBeInTheDocument();
    expect(bar.getByRole('button', { name: 'Next commit' })).toBeDisabled();
    await user.click(bar.getByRole('button', { name: 'Previous commit' }));
    expect(bar.getByTestId('screen-title')).toHaveTextContent('feat: rotate on expiry');
  });

  it('holds the header, the revs and the tree in the side sheet, and a rev pick closes it', async () => {
    const user = userEvent.setup();
    await pushChanges(user);
    const sheet = await openSheet(user);
    expect(sheet.getByRole('button', { name: 'Refresh' })).toBeInTheDocument();
    expect(sheet.getByRole('tree')).toBeInTheDocument();
    const nav = within(sheet.getByRole('navigation', { name: 'Changes to show' }));
    await user.click(nav.getByRole('button', { name: /^Uncommitted/ }));
    // The strip names what is shown. A rev that is not a commit has no commit
    // to step to.
    const bar = screenStrip();
    expect(screen.queryByRole('dialog', { name: 'More' })).toBeNull();
    expect(bar.getByTestId('screen-title')).toHaveTextContent('Uncommitted');
    expect(bar.queryByRole('button', { name: 'Next commit' })).toBeNull();
    expect(await screen.findByText('new tokens')).toBeInTheDocument();
  });

  it('closes the side sheet on its Close button', async () => {
    const user = userEvent.setup();
    await pushChanges(user);
    const sheet = await openSheet(user);
    await user.click(sheet.getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog', { name: 'More' })).toBeNull();
  });

  it('picks a file from the sheet, which closes it and scrolls to the file', async () => {
    const user = userEvent.setup();
    await pushChanges(user);
    const region = screen.getByRole('region', { name: 'auth/expiry.py' });
    const tree = (await openSheet(user)).getByRole('tree');
    await user.click(within(tree).getByRole('treeitem', { name: /auth/ }));
    await user.click(within(tree).getByRole('treeitem', { name: /expiry\.py/ }));
    expect(screen.queryByRole('dialog', { name: 'More' })).toBeNull();
    expect(scrolled).toHaveBeenCalledTimes(1);
    expect(scrolled.mock.contexts[0]).toBe(region);
  });
});

describe('the Rendered view', () => {
  const lines = (n: number) => Array.from({ length: n }, (_, i) => `line ${i + 1}`).join('\n');
  const PLACE = `  Scenario: Place an order
    Given a cart with 2 items
    Then I see a receipt
`;
  const CANCEL = `  Scenario: Cancel an order
    Given a placed order
    Then it is refunded
`;
  const FEATURE = (place: string) =>
    `Feature: Checkout\n  A cart becomes an order\n  at checkout.\n\n  An order can be cancelled.\n\n${place}\n${CANCEL}`;

  /** Open the tab on two uncommitted files, a `.py` and a `.feature`, with their versions. */
  async function openWithVersions(feature: { old: string; new: string }) {
    return openChanges((c) => {
      c.diffs.uncommitted = [file('auth/tokens.py', 'tokens'), file('specs/checkout.feature', 'x')];
      c.files = {
        uncommitted: {
          'auth/tokens.py': {
            old: `${lines(10)}\nold tokens\n`,
            new: `${lines(10)}\nnew tokens\n`,
          },
          'specs/checkout.feature': feature,
        },
      };
    });
  }

  async function view(panel: HTMLElement, path: string) {
    const head = await within(panel).findByRole('region', { name: path });
    await userEvent.click(within(head).getByRole('button', { name: `View ${path}` }));
    return screen.findByRole('dialog', { name: `${path}, rendered` });
  }

  const mode = (dialog: HTMLElement, name: string) =>
    userEvent.click(within(dialog).getByRole('button', { name }));
  const showDisplay = (dialog: HTMLElement, name: string) =>
    userEvent.click(
      within(within(dialog).getByRole('group', { name: 'Display' })).getByRole('button', { name }),
    );
  const kinds = (dialog: HTMLElement) =>
    within(dialog)
      .getAllByTestId('diff-row')
      .map((row) => row.getAttribute('data-kind'));
  const scenario = (within_: HTMLElement, name: string) =>
    within(within_).queryByRole('region', { name: `Scenario: ${name}` });
  const steps = (dialog: HTMLElement) =>
    within(dialog)
      .getAllByTestId('feature-step')
      .map((s) => `${s.getAttribute('data-kind')} ${s.textContent}`);
  /** Each contents link, after its change mark's name when it has one. */
  const contents = (dialog: HTMLElement) =>
    within(within(dialog).getByRole('navigation', { name: 'Contents' }))
      .getAllByRole('link')
      .map((link) =>
        [link.previousElementSibling?.getAttribute('aria-label'), link.textContent]
          .filter(Boolean)
          .join(' '),
      );

  const scrolled = vi.fn();
  beforeEach(() => {
    Element.prototype.scrollIntoView = scrolled;
  });
  afterEach(() => {
    scrolled.mockReset();
  });

  it('draws a text file as lines, in each mode', async () => {
    const { panel } = await openWithVersions({ old: FEATURE(PLACE), new: FEATURE(PLACE) });
    const dialog = await view(panel, 'auth/tokens.py');
    expect(within(dialog).getByRole('button', { name: 'Changes only' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    // The text plugin draws one way, so it has no Display control.
    expect(within(dialog).queryByRole('group', { name: 'Display' })).toBeNull();

    // Changes only: the change and three lines of context above it.
    await waitFor(() =>
      expect(kinds(dialog)).toEqual(['context', 'context', 'context', 'remove', 'add']),
    );
    await mode(dialog, 'Whole file');
    expect(kinds(dialog)).toHaveLength(12);
    await mode(dialog, 'Final');
    expect(kinds(dialog)).toEqual(Array(11).fill('context'));
    expect(within(dialog).getAllByTestId('diff-row').at(-1)).toHaveTextContent('11 new tokens');
  });

  it('draws a feature file as scenarios, in each mode', async () => {
    const edited = PLACE.replace('2 items', '3 items');
    const { panel } = await openWithVersions({ old: FEATURE(PLACE), new: FEATURE(edited) });
    const dialog = await view(panel, 'specs/checkout.feature');

    // Changes only: the feature's head, a count, and the changed scenario alone.
    await within(dialog).findByTestId('feature-view');
    expect(within(dialog).getByRole('button', { name: 'Formatted' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(dialog).toHaveTextContent('Feature: Checkout');
    expect(dialog).toHaveTextContent('Scenarios: 0 added, 1 changed, 0 removed');
    expect(scenario(dialog, 'Cancel an order')).toBeNull();
    expect(scenario(dialog, 'Place an order')).toHaveTextContent('Changed');
    expect(steps(dialog)).toEqual([
      'remove -Given a cart with 2 items',
      'add +Given a cart with 3 items',
      'context  Then I see a receipt',
    ]);
    // The description: one paragraph per blank-line block.
    expect(within(dialog).getByText('A cart becomes an order at checkout.').tagName).toBe('P');
    expect(within(dialog).getByText('An order can be cancelled.').tagName).toBe('P');

    // The contents list the scenarios shown, each with its change, and a link
    // scrolls to its heading.
    expect(contents(dialog)).toEqual(['Changed Place an order']);
    await userEvent.click(within(dialog).getByRole('link', { name: 'Place an order' }));
    expect(scrolled.mock.contexts).toEqual([
      within(dialog).getByRole('heading', { name: /Scenario: Place an order/ }),
    ]);

    await mode(dialog, 'Whole file');
    expect(scenario(dialog, 'Cancel an order')).not.toBeNull();
    expect(steps(dialog)).toHaveLength(5);
    expect(contents(dialog)).toEqual(['Changed Place an order', 'Cancel an order']);

    await mode(dialog, 'Final');
    expect(steps(dialog)).toEqual([
      'context  Given a cart with 3 items',
      'context  Then I see a receipt',
      'context  Given a placed order',
      'context  Then it is refunded',
    ]);
    expect(dialog).not.toHaveTextContent('Changed');
  });

  it('pages a feature one scenario at a time, under its Background and its rule', async () => {
    const RULED = (partly: string) => `Feature: Checkout

  Background:
    Given a signed-in customer

${PLACE}
  Rule: Refunds

    Scenario: Refund in full
      Given a paid order
      Then it is refunded

    Scenario: Refund in part
      Given a paid order
      Then ${partly}
`;
    const { panel } = await openWithVersions({
      old: RULED('half is refunded'),
      new: RULED('half is returned'),
    });
    const dialog = await view(panel, 'specs/checkout.feature');
    await within(dialog).findByTestId('feature-view');
    await mode(dialog, 'Whole file');
    await showDisplay(dialog, 'Per scenario');
    const rule = () => within(dialog).queryByRole('region', { name: 'Rule: Refunds' });
    const previous = () => within(dialog).queryByRole('button', { name: 'Previous scenario' });
    const next = () => within(dialog).queryByRole('button', { name: 'Next scenario' });
    const background = () => within(dialog).queryByRole('region', { name: /^Background/ });

    expect(contents(dialog)).toEqual([
      'Background',
      'Place an order',
      'Changed Refunds',
      'Refund in full',
      'Changed Refund in part',
    ]);
    // The Background is no page of its own: it heads every scenario's page.
    expect(background()).not.toBeNull();
    expect(scenario(dialog, 'Place an order')).not.toBeNull();
    expect(rule()).toBeNull();
    expect(previous()).toBeDisabled();

    // A rule's link shows its first scenario, under the rule.
    await userEvent.click(within(dialog).getByRole('link', { name: 'Refunds' }));
    expect(scenario(dialog, 'Place an order')).toBeNull();
    expect(scenario(rule()!, 'Refund in full')).not.toBeNull();
    expect(scenario(rule()!, 'Refund in part')).toBeNull();
    expect(background()).not.toBeNull();
    await userEvent.click(next()!);
    expect(scenario(rule()!, 'Refund in part')).not.toBeNull();
    expect(next()).toBeDisabled();
    await userEvent.click(within(dialog).getByRole('link', { name: 'Background' }));
    expect(scenario(dialog, 'Place an order')).not.toBeNull();
    await userEvent.click(within(dialog).getByRole('link', { name: 'Refund in full' }));
    expect(scenario(rule()!, 'Refund in full')).not.toBeNull();

    // Changes only hides the scenario shown, so the first it shows takes its
    // place; one scenario needs no Previous or Next.
    await mode(dialog, 'Changes only');
    expect(scenario(rule()!, 'Refund in part')).not.toBeNull();
    expect(next()).toBeNull();
  });

  it('draws a feature file as its lines in Raw', async () => {
    const edited = PLACE.replace('2 items', '3 items');
    const { panel } = await openWithVersions({ old: FEATURE(PLACE), new: FEATURE(edited) });
    const dialog = await view(panel, 'specs/checkout.feature');
    await within(dialog).findByTestId('feature-view');
    await showDisplay(dialog, 'Raw');
    expect(within(dialog).queryByTestId('feature-view')).toBeNull();
    const changed = within(dialog)
      .getAllByTestId('diff-row')
      .filter((row) => row.getAttribute('data-kind') !== 'context')
      .map((row) => `${row.getAttribute('data-kind')} ${row.textContent}`);
    expect(changed).toEqual([
      expect.stringMatching(/^remove .*Given a cart with 2 items$/),
      expect.stringMatching(/^add .*Given a cart with 3 items$/),
    ]);
  });

  it('falls back to text for a feature file that does not parse, and closes on ×', async () => {
    const { panel } = await openWithVersions({
      old: FEATURE(PLACE),
      new: 'Given a step before any feature\n',
    });
    const dialog = await view(panel, 'specs/checkout.feature');
    expect(await within(dialog).findByRole('status')).toHaveTextContent(
      'Not valid Gherkin, so shown as text: (1:1)',
    );
    expect(kinds(dialog)).toContain('add');

    await userEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog', { name: /rendered/ })).toBeNull();
  });

  /** Each image's source: the side and path of a raw URL, else the src as written. */
  const imageSources = (dialog: HTMLElement) =>
    within(dialog)
      .getAllByRole('img')
      .map((img) => {
        const src = img.getAttribute('src')!;
        if (!src.startsWith('/api/')) return src;
        const q = new URL(src, 'http://x').searchParams;
        return [q.get('side'), q.get('path'), q.get('oldPath')].filter(Boolean).join(' ');
      });

  it('draws a markdown file as its blocks, in each mode', async () => {
    const DOC = (first: string, second: string) =>
      [
        '# Rotation',
        'Tokens rotate. ![up](../up.png) ![web](https://x/y.png) ![out](../../out.png)',
        '## Expiry',
        `${first} ![shot](img/a.png)`,
        'The middle stays.',
        second,
        '## Grace',
        'The old token stays a minute.',
      ].join('\n\n') + '\n';
    const { panel } = await openChanges((c) => {
      c.diffs.uncommitted = [
        file('guide/rotation.md', 'x', { status: 'renamed', oldPath: 'docs/rotation.md' }),
      ];
      c.files = {
        uncommitted: {
          'guide/rotation.md': {
            old: DOC('A token expires after an hour.', 'It rotates once.'),
            new: DOC('A token expires after two hours.', 'It rotates twice.'),
          },
        },
      };
    });
    const dialog = await view(panel, 'guide/rotation.md');
    const blocks = () =>
      within(dialog)
        .getAllByTestId('markdown-block')
        .map((b) => `${b.getAttribute('data-kind')} ${b.textContent?.trim()}`);

    // Changes only: each edited paragraph as removed then added. Both runs sit
    // under one heading, shown once.
    expect(within(dialog).getByRole('button', { name: 'Formatted' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await waitFor(() =>
      expect(blocks()).toEqual([
        'context Expiry',
        'remove A token expires after an hour.',
        'add A token expires after two hours.',
        'remove It rotates once.',
        'add It rotates twice.',
      ]),
    );
    expect(within(dialog).getByRole('heading', { name: 'Expiry', level: 2 })).toBeVisible();
    // A relative ref resolves against its own side's folder: the old side was
    // `docs/`, the new one is `guide/`.
    expect(imageSources(dialog)).toEqual(['old docs/img/a.png', 'new guide/img/a.png']);

    await mode(dialog, 'Whole file');
    expect(blocks()).toEqual([
      'context Rotation',
      'context Tokens rotate.',
      'context Expiry',
      'remove A token expires after an hour.',
      'add A token expires after two hours.',
      'context The middle stays.',
      'remove It rotates once.',
      'add It rotates twice.',
      'context Grace',
      'context The old token stays a minute.',
    ]);
    // A URL passes through, and so does a ref that climbs out of the worktree.
    expect(imageSources(dialog)).toEqual([
      'new up.png',
      'https://x/y.png',
      '../../out.png',
      'old docs/img/a.png',
      'new guide/img/a.png',
    ]);

    await mode(dialog, 'Final');
    expect(within(dialog).getByRole('heading', { name: 'Rotation', level: 1 })).toBeVisible();
    expect(dialog).toHaveTextContent('two hours');
    expect(dialog).not.toHaveTextContent('an hour');

    await mode(dialog, 'Changes only');
    await showDisplay(dialog, 'Raw');
    expect(within(dialog).queryAllByTestId('markdown-block')).toEqual([]);
    expect(kinds(dialog)).toContain('remove');
  });

  it('reads a side past the size cap as empty, not as a new file', async () => {
    const { panel } = await openChanges((c) => {
      c.diffs.uncommitted = [file('specs/checkout.feature', 'x')];
      c.files = {
        uncommitted: {
          'specs/checkout.feature': { old: null, new: FEATURE(PLACE), truncated: true },
        },
      };
    });
    const dialog = await view(panel, 'specs/checkout.feature');
    expect(within(dialog).getByText(/One version is too large to show/)).toBeInTheDocument();
    expect(await within(dialog).findByTestId('feature-view')).toHaveAttribute(
      'data-change',
      'unchanged',
    );
  });

  it('opens a deleted file on its removed side, with Final disabled', async () => {
    const { panel } = await openChanges((c) => {
      c.diffs.uncommitted = [file('specs/checkout.feature', 'x', { status: 'deleted' })];
      c.files = { uncommitted: { 'specs/checkout.feature': { old: FEATURE(PLACE), new: null } } };
    });
    const dialog = await view(panel, 'specs/checkout.feature');
    expect(within(dialog).getByRole('button', { name: 'Final' })).toBeDisabled();
    await within(dialog).findByTestId('feature-view');
    expect(dialog).toHaveTextContent('Scenarios: 0 added, 0 changed, 2 removed');
  });
});

describe('change comments', () => {
  const AGENT = 'e5b1d8c3';
  const region = (panel: HTMLElement, path = 'auth/tokens.py') =>
    within(panel).getByRole('region', { name: path });
  const line = (panel: HTMLElement, name: string, path?: string) =>
    within(region(panel, path)).getByRole('button', { name });
  const selected = (panel: HTMLElement, path?: string) =>
    within(region(panel, path))
      .getAllByTestId('diff-row')
      .filter((row) => row.hasAttribute('data-selected'))
      .map((row) => row.textContent);
  const dock = () => screen.getByRole('region', { name: 'Change comments' });

  /** Select `name`'s line and add a comment that says `body`. */
  async function addComment(panel: HTMLElement, name: string, body: string) {
    await userEvent.click(line(panel, name));
    await userEvent.type(within(panel).getByRole('textbox'), body);
    await userEvent.click(within(panel).getByRole('button', { name: 'Add comment' }));
  }

  it('opens a comment box below a line whose number is clicked', async () => {
    const { panel } = await openChanges();
    expect(screen.queryByRole('region', { name: 'Change comments' })).toBeNull();

    await userEvent.click(line(panel, 'Line 5'));
    expect(within(panel).getByRole('textbox', { name: 'Comment on line 5' })).toHaveFocus();
    expect(selected(panel)).toEqual(['5+new tokens']);

    // Cancel puts the box away, and the selection with it.
    await userEvent.click(within(panel).getByRole('button', { name: 'Cancel' }));
    expect(within(panel).queryByRole('textbox')).toBeNull();
    expect(selected(panel)).toEqual([]);
  });

  it('selects a line from the keyboard, and Escape puts the box away', async () => {
    const { panel } = await openChanges();
    line(panel, 'Line 4').focus();
    await userEvent.keyboard('{Enter}');
    const box = within(panel).getByRole('textbox', { name: 'Comment on line 4' });
    expect(box).toHaveFocus();

    await userEvent.keyboard('{Escape}');
    expect(within(panel).queryByRole('textbox')).toBeNull();
    expect(selected(panel)).toEqual([]);
  });

  it('selects a range with a pointer drag down the numbers', async () => {
    const { panel } = await openChanges();
    const rows = within(region(panel)).getAllByTestId('diff-row');
    await userEvent.pointer([
      { keys: '[MouseLeft>]', target: line(panel, 'Line 4') },
      { target: rows[1]! },
      { target: rows[2]! },
      { keys: '[/MouseLeft]' },
    ]);
    expect(selected(panel)).toEqual(['44 import os', '5-old tokens', '5+new tokens']);
    expect(within(panel).getByRole('textbox', { name: 'Comment on lines 4-5' })).toBeVisible();
  });

  it('extends the selection with Shift and a click, and names an old line as one', async () => {
    const { panel } = await openChanges();
    await userEvent.click(line(panel, 'Old line 5'));
    expect(within(panel).getByRole('textbox', { name: 'Comment on old line 5' })).toBeVisible();

    const user = userEvent.setup();
    await user.keyboard('{Shift>}');
    await user.click(line(panel, 'Line 4'));
    await user.keyboard('{/Shift}');
    expect(selected(panel)).toEqual(['44 import os', '5-old tokens']);
    expect(within(panel).getByRole('textbox', { name: 'Comment on line 4' })).toBeVisible();
  });

  it('keeps an added comment below its lines, and counts it in the dock', async () => {
    const { panel } = await openChanges();
    await addComment(panel, 'Line 5', 'rotate the old one too');
    expect(within(panel).queryByRole('textbox')).toBeNull();
    expect(within(region(panel)).getByText('rotate the old one too')).toBeVisible();
    expect(dock()).toHaveTextContent('1 comment to Rotate auth tokens');

    // Edit opens the box on the same lines, with the text.
    await userEvent.click(within(region(panel)).getByRole('button', { name: 'Edit' }));
    const box = within(panel).getByRole('textbox', { name: 'Comment on line 5' });
    expect(box).toHaveValue('rotate the old one too');
    await userEvent.type(box, ', please');
    await userEvent.click(within(panel).getByRole('button', { name: 'Add comment' }));
    expect(within(region(panel)).getByText('rotate the old one too, please')).toBeVisible();
    expect(dock()).toHaveTextContent('1 comment');

    await userEvent.click(within(region(panel)).getByRole('button', { name: 'Delete' }));
    expect(screen.queryByRole('region', { name: 'Change comments' })).toBeNull();
  });

  it('draws a comment under edit once, when its box moves to another file', async () => {
    const { panel } = await openChanges();
    await addComment(panel, 'Line 5', 'rotate the old one too');
    await userEvent.click(within(region(panel)).getByRole('button', { name: 'Edit' }));
    await userEvent.click(line(panel, 'Line 4', 'auth/rotate.py'));
    expect(within(region(panel, 'auth/rotate.py')).getByRole('textbox')).toHaveValue(
      'rotate the old one too',
    );
    expect(within(region(panel)).queryByRole('button', { name: 'Edit' })).toBeNull();
  });

  it('holds the comments and the open box through a tab close, and counts those out of view', async () => {
    // The commit changes the same file and the same lines as the uncommitted work.
    const { panel } = await openChanges((c) => {
      c.diffs[SHA] = [file('auth/tokens.py', 'tokens')];
    });
    await waitFor(() => expect(rowTexts(panel, 'auth/tokens.py')).toHaveLength(3));
    await addComment(panel, 'Line 5', 'rotate the old one too');
    await userEvent.click(line(panel, 'Line 4'));
    await userEvent.type(within(panel).getByRole('textbox'), 'half a thought');
    // A second try at the range keeps what was typed.
    await userEvent.click(line(panel, 'Old line 5'));
    expect(within(panel).getByRole('textbox')).toHaveValue('half a thought');
    await userEvent.click(line(panel, 'Line 4'));

    // The node stays expanded behind the panel, so its link opens the tab again.
    await userEvent.click(screen.getByRole('button', { name: /^Close northwind delta/ }));
    expect(screen.queryByTestId('changes-tab')).toBeNull();
    await userEvent.click(within(expanded()).getByRole('link', { name: 'Changes' }));
    const again = screen.getByTestId('panel');
    await pick(/^Uncommitted/);
    await waitFor(() => expect(rowTexts(again, 'auth/tokens.py')).toHaveLength(3));
    expect(within(region(again)).getByText('rotate the old one too')).toBeVisible();
    expect(within(again).getByRole('textbox', { name: 'Comment on line 4' })).toHaveValue(
      'half a thought',
    );

    // Another rev draws neither, on the same lines of the same file. The dock
    // still counts the comment.
    await pick(/rotate on expiry/);
    await waitFor(() => expect(current()).toBe('c0ffee1 feat: rotate on expiry'));
    await waitFor(() => expect(rowTexts(again, 'auth/tokens.py')).toHaveLength(3));
    expect(within(again).queryByText('rotate the old one too')).toBeNull();
    expect(within(again).queryByRole('textbox')).toBeNull();
    expect(dock()).toHaveTextContent('1 comment');
  });

  it('does not draw a comment on lines that no longer read as they did', async () => {
    const { server, panel } = await openChanges();
    await addComment(panel, 'Line 5', 'rotate the old one too');
    // The agent edits the line. Its number stays, and its text does not.
    act(() => {
      server.change({ kind: 'worktree', ids: [DELTA] }, (w) => {
        w.changes[DELTA]!.diffs.uncommitted = [file('auth/tokens.py', 'again')];
      });
    });
    await waitFor(() => expect(rowTexts(panel, 'auth/tokens.py')[2]).toContain('new again'));
    expect(within(panel).queryByText('rotate the old one too')).toBeNull();
    expect(dock()).toHaveTextContent('1 comment');
  });

  it('posts every comment in one request, and clears the set', async () => {
    const { server, panel } = await openChanges();
    await userEvent.pointer([
      { keys: '[MouseLeft>]', target: line(panel, 'Line 4') },
      { target: within(region(panel)).getAllByTestId('diff-row')[2]! },
      { keys: '[/MouseLeft]' },
    ]);
    await userEvent.type(within(panel).getByRole('textbox'), 'why both?');
    await userEvent.click(within(panel).getByRole('button', { name: 'Add comment' }));
    await pick(/rotate on expiry/);
    await waitFor(() => expect(rowTexts(panel, 'auth/expiry.py')).toHaveLength(3));
    await userEvent.click(line(panel, 'Old line 5', 'auth/expiry.py'));
    await userEvent.type(within(panel).getByRole('textbox'), 'keep this');
    await userEvent.click(within(panel).getByRole('button', { name: 'Add comment' }));
    expect(dock()).toHaveTextContent('2 comments');
    // A box still open is not part of the post, and the post does not take it.
    await userEvent.click(line(panel, 'Line 4', 'auth/expiry.py'));
    await userEvent.type(within(panel).getByRole('textbox'), 'not yet');
    expect(
      within(region(panel, 'auth/expiry.py')).getByRole('button', { name: 'Edit' }),
    ).toBeDisabled();

    await userEvent.click(within(dock()).getByRole('button', { name: 'Post comments' }));
    await waitFor(() =>
      expect(screen.queryByRole('region', { name: 'Change comments' })).toBeNull(),
    );

    const sent = server.requests.find((r) => r.path === `/api/worktrees/${DELTA}/comments`);
    expect(sent?.method).toBe('POST');
    expect(sent?.body).toEqual({
      comments: [
        {
          id: expect.any(String),
          rev: 'uncommitted',
          path: 'auth/tokens.py',
          side: 'new',
          startLine: 4,
          endLine: 5,
          lines: [' import os', '-old tokens', '+new tokens'],
          body: 'why both?',
        },
        {
          id: expect.any(String),
          rev: SHA,
          path: 'auth/expiry.py',
          side: 'old',
          startLine: 5,
          endLine: 5,
          lines: ['-old expiry'],
          body: 'keep this',
        },
      ],
    });
    expect(within(panel).queryByText('keep this')).toBeNull();
    expect(within(panel).getByRole('textbox')).toHaveValue('not yet');
  });

  it('cannot post, and says why, when no agent runs in the worktree', async () => {
    const { server, panel } = await openChanges();
    act(() => {
      server.change({ kind: 'agent', ids: [AGENT] }, (w) => {
        w.agents[AGENT] = { ...w.agents[AGENT]!, state: 'exited' };
      });
    });
    await addComment(panel, 'Line 5', 'rotate the old one too');
    await waitFor(() =>
      expect(dock()).toHaveTextContent('1 comment. No agent is running in this worktree.'),
    );
    expect(within(dock()).getByRole('button', { name: 'Post comments' })).toBeDisabled();

    // Clear is the other way out.
    await userEvent.click(within(dock()).getByRole('button', { name: 'Clear' }));
    expect(within(panel).queryByText('rotate the old one too')).toBeNull();
    expect(screen.queryByRole('region', { name: 'Change comments' })).toBeNull();
  });

  it('clears the set and names the agent a post did not reach', async () => {
    const { server, panel } = await openChanges();
    act(() => {
      server.change({ kind: 'agent', ids: ['free1'] }, (w) => {
        w.agents.free1 = { ...w.agents[AGENT]!, id: 'free1', taskId: '' };
      });
    });
    server.hostRefuses.free1 = 'agent free1 has exited';
    await addComment(panel, 'Line 5', 'rotate the old one too');
    await waitFor(() => expect(dock()).toHaveTextContent('1 comment to Rotate auth tokens, free1'));

    await userEvent.click(within(dock()).getByRole('button', { name: 'Post comments' }));
    const notice = await within(panel).findByRole('status');
    expect(notice).toHaveTextContent('Posted, but not to free1 (agent free1 has exited).');
    expect(screen.queryByRole('region', { name: 'Change comments' })).toBeNull();

    await userEvent.click(within(notice).getByRole('button', { name: 'Dismiss' }));
    expect(within(panel).queryByRole('status')).toBeNull();
  });

  it('keeps the comments when the post is refused', async () => {
    const { server, panel } = await openChanges();
    await addComment(panel, 'Line 5', 'rotate the old one too');
    server.refuse(/POST \/api\/worktrees\/[^/]+\/comments/, {
      status: 409,
      code: 'agent_exited',
      message: 'Agent e5b1d8c3 has exited',
    });
    await userEvent.click(within(dock()).getByRole('button', { name: 'Post comments' }));
    expect(await within(dock()).findByRole('alert')).toHaveTextContent(/exited/);
    expect(within(region(panel)).getByText('rotate the old one too')).toBeVisible();
    expect(dock()).toHaveTextContent('1 comment');
  });

  describe('row draws', () => {
    /** Both files of the diff, in syntax colour: no draw is still to come. */
    async function settled(panel: HTMLElement) {
      await highlighted(panel, 'auth/tokens.py');
      await highlighted(panel, 'auth/rotate.py');
      // The counter is live: the rows drew to get here.
      expect(draws.count).toBeGreaterThan(0);
    }

    it('draws no row for a key in an open comment box', async () => {
      const { panel } = await openChanges();
      await settled(panel);
      await userEvent.click(line(panel, 'Line 5'));
      const box = within(panel).getByRole('textbox');

      draws.count = 0;
      await userEvent.type(box, 'why');
      expect(box).toHaveValue('why');
      expect(draws.count).toBe(0);
    });

    it('draws only the rows a drag adds to the selection', async () => {
      const { panel } = await openChanges();
      await settled(panel);
      const rows = within(region(panel)).getAllByTestId('diff-row');
      const user = userEvent.setup();
      await user.pointer({ keys: '[MouseLeft>]', target: line(panel, 'Line 4') });
      expect(selected(panel)).toEqual(['44 import os']);

      draws.count = 0;
      await user.pointer([{ target: rows[1]! }, { target: rows[2]! }]);
      expect(selected(panel)).toEqual(['44 import os', '5-old tokens', '5+new tokens']);
      // One draw for each of the two rows the drag reached, of the six in view.
      expect(draws.count).toBe(2);
      await user.pointer({ keys: '[/MouseLeft]' });
    });

    it('draws no row of the tab when a Rendered view opens', async () => {
      const { panel } = await openChanges();
      await settled(panel);

      draws.count = 0;
      const files = within(panel).getByRole('list', { name: 'Files' });
      await userEvent.click(within(files).getByRole('button', { name: 'View auth/tokens.py' }));
      // The fake has no versions for this file, so the view draws no row of its own.
      const dialog = await screen.findByRole('dialog', { name: 'auth/tokens.py, rendered' });
      await within(dialog).findByText('Binary file, not shown.');
      expect(draws.count).toBe(0);
    });

    it('draws no row for a world change', async () => {
      const { server, panel } = await openChanges();
      await addComment(panel, 'Line 5', 'rotate the old one too');
      await settled(panel);
      expect(dock()).toHaveTextContent('1 comment to Rotate auth tokens');
      const taskId = server.world.agents[AGENT]!.taskId;

      draws.count = 0;
      act(() => {
        server.change({ kind: 'task', ids: [taskId] }, (w) => {
          w.tasks[taskId] = { ...w.tasks[taskId]!, title: 'Rotate every token' };
        });
      });
      await waitFor(() => expect(dock()).toHaveTextContent('1 comment to Rotate every token'));
      expect(draws.count).toBe(0);
    });
  });
});
