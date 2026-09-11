import type { Story } from '@ladle/react';
import { SplitChip } from './SplitChip';

export default { title: 'UI / SplitChip' };

/**
 * jsdom computes no layout, so the claims the suite cannot hold are the ones
 * framed here: that the divider reads as a divider, that the value holds its
 * width as digits change, and that the stale state is legible without colour.
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
      <div style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'center' }}>{children}</div>
    </div>
  );
}

/** Every tone at once: the set only reads as a set side by side. */
export const Tones: Story = () => (
  <Board>
    <Row label="rising utilisation">
      <SplitChip label="5h" title="5-hour limit: 7% used" tone="neutral">
        7%
      </SplitChip>
      <SplitChip label="5h" title="5-hour limit: 62% used" tone="neutral">
        62%
      </SplitChip>
      <SplitChip label="5h" title="5-hour limit: 84% used" tone="busy">
        84%
      </SplitChip>
      <SplitChip label="5h" title="5-hour limit: 96% used" tone="bad">
        96%
      </SplitChip>
    </Row>
    <Row label="the other tones">
      <SplitChip label="agents" title="3 of 5 agents working, 2 idle" tone="good">
        3/5
      </SplitChip>
      <SplitChip label="week" title="7-day limit: 24% used" tone="quiet">
        24%
      </SplitChip>
    </Row>
  </Board>
);

/**
 * Stale beside fresh. The question is whether the state survives without
 * colour: the divider dashes, which is the second channel DESIGN.md asks for.
 */
export const Stale: Story = () => (
  <Board>
    <Row label="fresh, then the same reading gone stale">
      <SplitChip label="5h" title="5-hour limit: 96% used, resets in 41m" tone="bad">
        96%
      </SplitChip>
      <SplitChip label="5h" title="5-hour limit: 96% used, as of 3h ago" tone="bad" stale>
        96%
      </SplitChip>
    </Row>
  </Board>
);

/**
 * The bar itself: three chips in a 40px strip on the raised ground, which is
 * where the sunken chip ground has to hold up.
 */
export const InTheTopBar: Story = () => (
  <div
    style={{
      display: 'flex',
      alignItems: 'center',
      gap: '12px',
      height: '40px',
      padding: '0 12px',
      background: 'var(--bg-raised)',
      borderBottom: '1px solid var(--border)',
      fontFamily: 'var(--font)',
    }}
  >
    <span style={{ fontSize: '14px', fontWeight: 700 }}>maelstrom</span>
    <span style={{ flex: 1 }} />
    <SplitChip label="5h" title="5-hour limit: 7% used, resets in 2h 40m">
      7%
    </SplitChip>
    <SplitChip label="week" title="7-day limit: 24% used, resets in 6d 1h">
      24%
    </SplitChip>
    <SplitChip label="agents" title="3 of 5 agents working, 2 idle" tone="busy">
      3/5
    </SplitChip>
  </div>
);

/**
 * The width claim: tabular digits, so the chip must not move as the number
 * ticks. Anything that shifts between these two rows is a bug.
 */
export const HoldsItsWidth: Story = () => (
  <Board>
    <Row label="one digit, then two, then three">
      <SplitChip label="5h" title="5-hour limit: 7% used">
        7%
      </SplitChip>
      <SplitChip label="5h" title="5-hour limit: 71% used">
        71%
      </SplitChip>
      <SplitChip label="5h" title="5-hour limit: 100% used" tone="bad">
        100%
      </SplitChip>
    </Row>
  </Board>
);
