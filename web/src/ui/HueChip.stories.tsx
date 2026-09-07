import type { Story } from '@ladle/react';
import { HueChip } from './HueChip';
import { PrChip } from '../shell/PrChip';
import { GitHubIcon } from '../shell/GitHubIcon';
import { ExternalLinkIcon } from '../shell/ExternalLinkIcon';
import type { PrState, Worktree } from '../protocol/entities';

export default { title: 'UI / HueChip' };

/**
 * The tones only read as a set when they are side by side, and the reveal only
 * reads when there is something beside it that must not move. Every story here
 * is framed for one of those two questions.
 */
function Board({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        padding: 'var(--space-5)',
        fontFamily: 'var(--font)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--space-5)',
        alignItems: 'flex-start',
      }}
    >
      {children}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
      <span
        style={{
          fontSize: 'var(--text-2xs)',
          letterSpacing: 'var(--tracking-micro)',
          textTransform: 'uppercase',
          color: 'var(--fg-faint)',
        }}
      >
        {label}
      </span>
      <div style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'center' }}>{children}</div>
    </div>
  );
}

const STATES: PrState[] = ['merged', 'ready', 'ci-failed', 'conflict', 'ci-running', 'unknown'];

function worktree(prState: PrState | '', prDraft = false): Worktree {
  return {
    id: 'northwind-delta',
    project: 'northwind',
    nato: 'delta',
    path: '/tmp/delta',
    branch: 'feat/thing',
    base: 'main',
    isClosed: false,
    dirtyFiles: 0,
    localCommits: 0,
    prNumber: 118,
    prUrl: 'https://github.com/acme/northwind/pull/118',
    prState,
    prDraft,
    appUrl: '',
    appRunning: false,
  } as Worktree;
}

/** Every PR reading at both sizes. Hover a large one to see it open. */
export const PrStates: Story = () => (
  <Board>
    <Row label="large — opens on hover and focus">
      {STATES.map((s) => (
        <PrChip key={s} worktree={worktree(s)} size="large" />
      ))}
      <PrChip worktree={worktree('ci-failed', true)} size="large" />
    </Row>
    <Row label="small — a dense meta line has no room to open into">
      {STATES.map((s) => (
        <PrChip key={s} worktree={worktree(s)} />
      ))}
      <PrChip worktree={worktree('ci-failed', true)} />
    </Row>
    <Row label="a PR number with no browse URL: text, not a dead link">
      <PrChip worktree={{ ...worktree('ready'), prUrl: '' }} size="large" />
    </Row>
  </Board>
);

/**
 * The chip on its own terms, with no PR in sight. The tone is a reading, so a
 * caller with entirely different states maps onto the same six.
 */
export const Tones: Story = () => (
  <Board>
    <Row label="the six readings">
      <HueChip brand={GitHubIcon} word="archived" tone="special" size="large" href="#" />
      <HueChip brand={GitHubIcon} word="passing" tone="good" size="large" href="#" />
      <HueChip brand={GitHubIcon} word="failing" tone="bad" size="large" href="#" />
      <HueChip brand={GitHubIcon} word="deploying" tone="busy" size="large" href="#" />
      <HueChip brand={GitHubIcon} word="checking" tone="neutral" size="large" href="#" />
      <HueChip brand={GitHubIcon} word="parked" tone="quiet" size="large" href="#" />
    </Row>
    <Row label="a second mark, for a chip that needs one">
      <HueChip
        brand={GitHubIcon}
        icon={ExternalLinkIcon}
        word="stale"
        tone="bad"
        size="large"
        href="#"
      >
        #4021
      </HueChip>
    </Row>
    <Row label="a long word must not clip: there is no width ceiling">
      <HueChip brand={GitHubIcon} word="merge conflicts" tone="bad" size="large" href="#" />
    </Row>
  </Board>
);

/**
 * The reason the small chip does not open. A canvas node is 220px wide and
 * clips; hover the chip and watch that nothing beside it moves.
 */
export const InAMetaRow: Story = () => (
  <Board>
    <Row label="a node-sized row — nothing may shift on hover">
      <div
        style={{
          width: 220,
          padding: '7px 10px',
          borderRadius: 'var(--radius)',
          background: 'var(--bg-raised)',
          border: '1px solid var(--border)',
          borderLeft: '4px solid var(--phase-build)',
          overflow: 'hidden',
        }}
      >
        <div style={{ fontSize: 'var(--text-ui)', marginBottom: 6 }}>Add the reveal chip</div>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            fontSize: 'var(--text-xs)',
            color: 'var(--fg-faint)',
          }}
        >
          <span style={{ fontFamily: 'var(--mono)' }}>NORT-12</span>
          <span style={{ fontFamily: 'var(--mono)' }}>delta</span>
          <PrChip worktree={worktree('ci-running')} />
          <span style={{ marginLeft: 'auto', color: 'var(--phase-build)' }}>BUILD</span>
        </div>
      </div>
    </Row>
  </Board>
);
