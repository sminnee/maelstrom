import { useCallback, useState, type RefObject } from 'react';
import type { Anchor } from '../../protocol/documents';
import { buildAnchor, locateQuote } from './anchor';
import { rangeToTextOffsets } from './domText';

/**
 * Turn a text selection inside the rendered document into an anchor into
 * the markdown source. The rendered text gives the quote and its context;
 * `locateQuote` finds the same words in the source.
 */
export function useSelectionComment(container: RefObject<HTMLElement | null>, markdown: string) {
  const [pending, setPending] = useState<Anchor | null>(null);

  const onMouseUp = useCallback(() => {
    const el = container.current;
    const selection = window.getSelection();
    if (!el || !selection || selection.rangeCount === 0 || selection.isCollapsed) return;
    const range = selection.getRangeAt(0);
    if (!el.contains(range.commonAncestorContainer)) return;
    const text = el.textContent ?? '';
    const { start, end } = rangeToTextOffsets(el, range);
    if (end <= start) return;
    const rendered = buildAnchor(text, start, end);
    const located = locateQuote(markdown, rendered);
    if (!located) return;
    setPending(buildAnchor(markdown, located.start, located.end));
  }, [container, markdown]);

  const clear = useCallback(() => setPending(null), []);
  return { pending, onMouseUp, clear };
}
