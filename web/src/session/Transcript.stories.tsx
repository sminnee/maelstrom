import type { Story } from '@ladle/react';
import { Markdown } from '../markdown/Markdown';
import { ledgerRun, markdownSample, mixedTranscript } from './transcript.fixture';
import { Transcript } from './Transcript';

export default { title: 'Session / Transcript' };

/**
 * The panel is resizable, so its width is the variable that matters most: the
 * transcript's own container query re-cuts the time gutter under 30rem. Every
 * story draws in a panel-shaped column rather than the full page.
 */
function Panel({ width = 460, children }: { width?: number; children: React.ReactNode }) {
  return (
    <div
      style={{
        width,
        maxWidth: '100%',
        height: '100vh',
        overflow: 'auto',
        borderLeft: '1px solid var(--border)',
        background: 'var(--bg)',
      }}
    >
      {children}
    </div>
  );
}

/** Prose against tool calls — the rank the panel is built on. */
export const Mixed: Story = () => (
  <Panel>
    <Transcript items={mixedTranscript} truncatedBefore={false} />
  </Panel>
);

/** A run of calls should read as one block, not as ruled paper. */
export const LedgerRun: Story = () => (
  <Panel>
    <Transcript items={ledgerRun} truncatedBefore={false} />
  </Panel>
);

/** The truncation note must not sit flush against the first row, or blur into the button below it. */
export const Truncated: Story = () => (
  <Panel>
    <Transcript items={mixedTranscript.slice(0, 4)} truncatedBefore hiddenCount={120} />
  </Panel>
);

/** Below 30rem the gutter collapses and the mark moves above the row. */
export const Narrow: Story = () => (
  <Panel width={360}>
    <Transcript items={mixedTranscript} truncatedBefore={false} />
  </Panel>
);

/** A wide panel: the measure must cap the prose rather than let it run. */
export const Wide: Story = () => (
  <Panel width={900}>
    <Transcript items={mixedTranscript} truncatedBefore={false} />
  </Panel>
);

/** Every markdown element at panel width, which is where the ramp is judged. */
export const Prose: Story = () => (
  <Panel width={720}>
    <div style={{ padding: 16 }}>
      <Markdown source={markdownSample} />
    </div>
  </Panel>
);
