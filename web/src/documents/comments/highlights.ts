import type { Anchor, Comment } from '../../protocol/documents';
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

/** Every unresolved comment's quote. */
const COMMENTS = 'document-comments';
/** The quote a composer is open for, painted stronger than the rest. */
const PENDING = 'document-pending';

function registry(): { highlights: HighlightRegistry; Highlight: HighlightCtor } | null {
  const css = (globalThis as { CSS?: { highlights?: HighlightRegistry } }).CSS;
  const ctor = (globalThis as { Highlight?: HighlightCtor }).Highlight;
  if (!css?.highlights || !ctor) return null;
  return { highlights: css.highlights, Highlight: ctor };
}

/**
 * Paint each comment's quote in the rendered document, and the pending
 * anchor in its own highlight. A no-op where the API is missing: the margin
 * still shows every comment with its quote.
 */
export function applyHighlights(
  container: HTMLElement,
  comments: Comment[],
  pending: Anchor | null = null,
): () => void {
  const api = registry();
  if (!api) return () => {};
  const text = container.textContent ?? '';
  const rangeFor = (anchor: Anchor): Range | null => {
    const span = locateQuote(text, { ...anchor, start: -1, end: -1 });
    return span ? textRange(container, span.start, span.end) : null;
  };
  const ranges = comments
    .filter((c) => !c.resolved)
    .map((c) => rangeFor(c.anchor))
    .filter((r): r is Range => r !== null);
  api.highlights.set(COMMENTS, new api.Highlight(...ranges));
  const pendingRange = pending ? rangeFor(pending) : null;
  if (pendingRange) api.highlights.set(PENDING, new api.Highlight(pendingRange));
  else api.highlights.delete(PENDING);
  return () => {
    api.highlights.delete(COMMENTS);
    api.highlights.delete(PENDING);
  };
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
