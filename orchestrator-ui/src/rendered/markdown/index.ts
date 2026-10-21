import type { RendererPlugin } from '../plugin';
import { MarkdownFileView } from './MarkdownFileView';

export const markdownPlugin: RendererPlugin = {
  id: 'markdown',
  matches: (path) => /\.(md|markdown)$/i.test(path),
  displays: [{ id: 'formatted', label: 'Formatted' }],
  View: MarkdownFileView,
};
