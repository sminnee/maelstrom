import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import { act } from 'react';
import userEvent from '@testing-library/user-event';
import type { FileDiff } from './protocol/entities';
import { expanded } from './test/appHelpers';
import type { FakeServer } from './test/fakeServer';
import { clickNode, renderApp } from './test/renderApp';

/** NORT-12's agent runs in delta, so its expanded node reaches delta's changes. */
const DELTA = 'northwind-delta';
const SHA = 'c0ffee1234567890c0ffee1234567890c0ffee12';

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
          author: 'Sam',
          date: '2026-09-02T09:00:00+12:00',
          filesChanged: 1,
        },
      ],
    },
    diffs: {
      uncommitted: [
        file('auth/tokens.py', 'tokens'),
        file('auth/rotate.py', 'rotate', { status: 'added' }),
      ],
      branch: [file('auth/expiry.py', 'expiry')],
      [SHA]: [file('auth/expiry.py', 'expiry')],
    },
  };
}

/** Open delta's Changes tab from NORT-12's node. `tweak` edits the seeded changes first. */
async function openChanges(tweak?: (changes: FakeServer['world']['changes'][string]) => void) {
  const { server } = await renderApp();
  seedChanges(server);
  tweak?.(server.world.changes[DELTA]!);
  clickNode('NORT-12');
  await userEvent.click(within(expanded()).getByRole('link', { name: 'Changes' }));
  const panel = screen.getByTestId('panel');
  if (!tweak) await within(panel).findByText('new tokens');
  return { server, panel };
}

const picker = () => screen.getByRole('combobox', { name: 'Changes to show' });

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
    expect(picker()).toHaveDisplayValue('Uncommitted (2)');
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
    const removed = within(panel).getByText('old tokens').closest('[data-kind]')!;
    expect(removed).toHaveAttribute('data-kind', 'remove');
    expect(removed).toHaveTextContent('5');
  });

  it('switches to the whole branch or one commit from the picker', async () => {
    const { server, panel } = await openChanges();
    expect(
      within(picker())
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['Uncommitted (2)', 'All commits (1)', 'c0ffee1 feat: rotate on expiry']);

    await userEvent.selectOptions(picker(), 'c0ffee1 feat: rotate on expiry');
    await within(panel).findByText('new expiry');
    expect(within(panel).queryByText('new tokens')).toBeNull();
    expect(server.requests.map((r) => r.path)).toContain(`/api/worktrees/${DELTA}/diff?rev=${SHA}`);
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
    act(() => {
      server.change({ kind: 'worktree', ids: [DELTA] }, (w) => {
        w.changes[DELTA]!.diffs.uncommitted = [file('auth/tokens.py', 'again')];
      });
    });
    await within(panel).findByText('new again');
  });

  it('falls back to the default rev when the picked commit leaves the branch', async () => {
    const { server, panel } = await openChanges();
    await userEvent.selectOptions(picker(), 'c0ffee1 feat: rotate on expiry');
    await within(panel).findByText('new expiry');
    // A rebase rewrote the commit, so its sha names nothing on the branch now.
    act(() => {
      server.change({ kind: 'worktree', ids: [DELTA] }, (w) => {
        w.changes[DELTA]!.changes.commits = [];
      });
    });
    await within(panel).findByText('new tokens');
    expect(picker()).toHaveDisplayValue('Uncommitted (2)');
  });

  it('reads the changes again on Refresh, with no notice', async () => {
    const { server, panel } = await openChanges();
    server.world.changes[DELTA]!.diffs.uncommitted = [file('auth/tokens.py', 'refreshed')];
    await userEvent.click(within(panel).getByRole('button', { name: 'Refresh' }));
    await within(panel).findByText('new refreshed');
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

  it('opens on all commits when nothing is uncommitted, and says when there are none', async () => {
    const { panel } = await openChanges((c) => {
      c.changes.dirtyFiles = [];
      c.changes.commits = [];
      c.diffs.branch = [];
    });
    expect(await within(panel).findByText('No commits ahead of main')).toBeInTheDocument();
    expect(picker()).toHaveDisplayValue('All commits (0)');
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
