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

  it('pushes a screen in the narrow layout', async () => {
    const { server } = await renderApp({ viewport: 'narrow' });
    seedChanges(server);
    await userEvent.click(screen.getByRole('button', { name: /Rotate auth tokens/ }));
    await userEvent.click(screen.getByRole('link', { name: 'Changes' }));
    expect(await screen.findByText('new expiry')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Back' })).toBeInTheDocument();
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
});
