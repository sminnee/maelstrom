import { useCallback, useEffect, useState, type RefObject } from 'react';
import type { Anchor } from '../../protocol/documents';
import { buildAnchor, locateQuote } from './anchor';
import { rangeToTextOffsets } from './domText';

/** An anchor plus where its first line sits, in px from the top of the body's visible area. */
export interface Placed {
  anchor: Anchor;
  top: number;
}

/**
 * Turn a text selection inside the rendered document into an anchor into the
 * markdown source. `selection` follows the live selection so the margin can
 * offer a comment at once; `startComment` moves it to `pending` and clears
 * the selection, so the pending highlight takes its place.
 */
export function useSelectionComment(container: RefObject<HTMLElement | null>, markdown: string) {
  const [selection, setSelection] = useState<Placed | null>(null);
  const [pending, setPending] = useState<Placed | null>(null);

  useEffect(() => {
    const el = container.current;
    const onChange = () => setSelection(placedSelection(container.current, markdown));
    document.addEventListener('selectionchange', onChange);
    // The offset is measured against the visible area, so a scroll moves it.
    el?.addEventListener('scroll', onChange);
    return () => {
      document.removeEventListener('selectionchange', onChange);
      el?.removeEventListener('scroll', onChange);
    };
  }, [container, markdown]);

  const startComment = useCallback(() => {
    if (!selection) return;
    setPending(selection);
    setSelection(null);
    window.getSelection()?.removeAllRanges();
  }, [selection]);

  const clear = useCallback(() => setPending(null), []);

  // Esc cancels the composer from anywhere, not only while it has focus.
  useEffect(() => {
    if (!pending) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setPending(null);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [pending]);
  return { selection, pending, startComment, clear };
}

/** The current selection as an anchor into the source, or null when it is not one. */
function placedSelection(el: HTMLElement | null, markdown: string): Placed | null {
  const selection = window.getSelection();
  if (!el || !selection || selection.rangeCount === 0 || selection.isCollapsed) return null;
  const range = selection.getRangeAt(0);
  if (!el.contains(range.commonAncestorContainer)) return null;
  const text = el.textContent ?? '';
  const { start, end } = rangeToTextOffsets(el, range);
  if (end <= start) return null;
  const rendered = buildAnchor(text, start, end);
  const located = locateQuote(markdown, rendered);
  if (!located) return null;
  return {
    anchor: buildAnchor(markdown, located.start, located.end),
    top: topOf(el, range),
  };
}

/**
 * The selection's first line, in px from the top of the body's visible area.
 * The margin beside the body positions against the same edge, so the two
 * agree whatever the body has scrolled to.
 */
function topOf(el: HTMLElement, range: Range): number {
  const rect = range.getClientRects?.()[0] ?? range.getBoundingClientRect?.();
  if (!rect) return 0;
  return Math.max(0, rect.top - el.getBoundingClientRect().top);
}
