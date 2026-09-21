import type { Story } from '@ladle/react';
import { Markdown } from '../markdown/Markdown';
import {
  quietTranscript,
  quietShort,
  quietBlockElements,
  ledgerRun,
  markdownSample,
  mixedTranscript,
} from './transcript.fixture';
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

/**
 * The two ranks of prose. The question this story answers is whether the
 * quiet run reads as quiet or as unreadable — check both schemes, and check
 * the user's turn still holds full rank against its accent wash.
 */
export const QuietBlocks: Story = () => (
  <Panel>
    <Transcript items={quietTranscript} truncatedBefore={false} />
  </Panel>
);

/** The quiet run at the narrow break, where 13px muted is hardest to read. */
export const QuietBlocksNarrow: Story = () => (
  <Panel width={360}>
    <Transcript items={quietTranscript} truncatedBefore={false} />
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

/** A quiet block long enough to clamp: does the fade read as "there is more", or as a bug? */
export const QuietClamped: Story = () => (
  <Panel>
    <Transcript items={quietTranscript} truncatedBefore={false} />
  </Panel>
);

/**
 * A quiet block opening with a heading, list, fence and table in turn — the
 * highest-risk case, since a `max-height` clamp cannot know where a block
 * boundary falls.
 */
export const QuietBlockElements: Story = () => (
  <Panel>
    <Transcript items={quietBlockElements} truncatedBefore={false} />
  </Panel>
);

/** A one-line quiet block: the clamp must offer no control here. */
export const QuietShort: Story = () => (
  <Panel>
    <Transcript items={quietShort} truncatedBefore={false} />
  </Panel>
);

/**
 * Quiet prose inside an open `skill` row, on `--bg-sunken`. Point a contrast
 * tool at this one in the light scheme — `--fg-faint` on `--bg-sunken` is
 * 4.15:1 there, under AA, which is why `.skill[open]` re-points
 * `--fg-recessed` to `--fg-muted`.
 */
export const QuietOnSunken: Story = () => (
  <Panel>
    <Transcript
      truncatedBefore={false}
      items={[
        {
          id: 'skill-1',
          ts: '',
          type: 'skill',
          skill: 'mael',
          markdown:
            '<user-attention low>\nQuiet prose inside a skill body, on the sunken ground the open row sets. Long enough to clamp and show the fade against that ground rather than the panel background.',
        },
      ]}
    />
  </Panel>
);

