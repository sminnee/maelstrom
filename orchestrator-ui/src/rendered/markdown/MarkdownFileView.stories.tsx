import type { Story } from '@ladle/react';
import type { Mode } from '../plugin';
import { NO_BYTES } from '../storyProps';
import { MarkdownFileView } from './MarkdownFileView';
import { NEW_MARKDOWN, OLD_MARKDOWN } from './markdown.fixture';

export default { title: 'Changes / Rendered view' };

const PATH = 'docs/rotation.md';

const markdown =
  (mode: Mode): Story =>
  () => (
    <MarkdownFileView {...NO_BYTES} old={OLD_MARKDOWN} new={NEW_MARKDOWN} mode={mode} path={PATH} />
  );

export const MarkdownChangesOnly = markdown('changes');
export const MarkdownWholeFile = markdown('whole');
export const MarkdownFinal = markdown('final');

/** A new file: one added document. */
export const MarkdownAddedFile: Story = () => (
  <MarkdownFileView {...NO_BYTES} old={null} new={NEW_MARKDOWN} mode="whole" path={PATH} />
);
