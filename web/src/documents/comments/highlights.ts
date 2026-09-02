import type { Comment } from '../../protocol/documents';
import { locateQuote } from './anchor';

// The CSS Custom Highlight API: `CSS.highlights` and `Highlight` are not in
// every browser's lib.dom yet, so they are reached through minimal shapes.
interface HighlightLike {
  add(range: Range): void;
}
interface HighlightRegistry {
  set(name: string, highlight: HighlightLike): void;
  delete(name: string): void;
}
type HighlightCtor = new (...ranges: Range[]) => HighlightLike;

const HIGHLIGHT_NAME = 'document-comments';

function registry(): { highlights: HighlightRegistry; Highlight: HighlightCtor } | null {
  const css = (globalThis as { CSS?: { highlights?: HighlightRegistry } }).CSS;
  const ctor = (globalThis as { Highlight?: HighlightCtor }).Highlight;
  if (!css?.highlights || !ctor) return null;
  return { highlights: css.highlights, Highlight: ctor };
}

/**
 * Paint each comment's quote in the rendered document. A no-op where the
 * API is missing: the margin still shows every comment with its quote.
 */
export function applyHighlights(container: HTMLElement, comments: Comment[]): () => void {
  const api = registry();
  if (!api) return () => {};
  const text = container.textContent ?? '';
  const ranges: Range[] = [];
  for (const comment of comments) {
    if (comment.resolved) continue;
    const span = locateQuote(text, { ...comment.anchor, start: -1, end: -1 });
    if (!span) continue;
    const range = textRange(container, span.start, span.end);
    if (range) ranges.push(range);
  }
  api.highlights.set(HIGHLIGHT_NAME, new api.Highlight(...ranges));
  return () => api.highlights.delete(HIGHLIGHT_NAME);
}

/** A Range covering `container.textContent[start, end)`. */
function textRange(container: HTMLElement, start: number, end: number): Range | null {
  const range = document.createRange();
  let offset = 0;
  let placedStart = false;
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const length = node.textContent?.length ?? 0;
    if (!placedStart && start < offset + length) {
      range.setStart(node, start - offset);
      placedStart = true;
    }
    if (placedStart && end <= offset + length) {
      range.setEnd(node, end - offset);
      return range;
    }
    offset += length;
  }
  return null;
}
