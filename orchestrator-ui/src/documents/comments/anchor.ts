import type { Anchor } from '../../protocol/documents';

/** How much text each side of a quote the anchor keeps, to tell repeats apart. */
export const CONTEXT_CHARS = 32;

export interface Span {
  start: number;
  end: number;
}

/** An anchor for `source[start, end)`: the quote plus the context around it. */
export function buildAnchor(source: string, start: number, end: number): Anchor {
  return {
    quote: source.slice(start, end),
    prefix: source.slice(Math.max(0, start - CONTEXT_CHARS), start),
    suffix: source.slice(end, end + CONTEXT_CHARS),
    start,
    end,
  };
}

/**
 * Where an anchor sits in `source` now. The cached offsets win when they
 * still hold the quote; otherwise the quote whose context matches best;
 * otherwise the first quote; otherwise null, and the comment is unanchored.
 */
export function locateQuote(source: string, anchor: Anchor): Span | null {
  const { quote } = anchor;
  if (!quote) return null;
  const context = anchor.prefix.length + anchor.suffix.length;
  const cachedHolds = source.slice(anchor.start, anchor.end) === quote;
  // The cached offsets win outright only when the context agrees too (or there
  // is none); otherwise they compete with every other occurrence.
  if (cachedHolds && contextScore(source, anchor.start, quote.length, anchor) === context)
    return { start: anchor.start, end: anchor.end };

  const candidates: number[] = [];
  for (let i = source.indexOf(quote); i !== -1; i = source.indexOf(quote, i + 1))
    candidates.push(i);
  if (candidates.length === 0) return null;

  let best = candidates[0]!;
  let bestScore = -1;
  for (const at of candidates) {
    const score = contextScore(source, at, quote.length, anchor);
    if (score > bestScore) {
      best = at;
      bestScore = score;
    }
  }
  return { start: best, end: best + quote.length };
}

/** How many context characters match, counted outward from the quote. */
function contextScore(source: string, at: number, length: number, anchor: Anchor): number {
  let score = 0;
  for (let i = 1; i <= anchor.prefix.length; i += 1) {
    if (source[at - i] !== anchor.prefix[anchor.prefix.length - i]) break;
    score += 1;
  }
  for (let i = 0; i < anchor.suffix.length; i += 1) {
    if (source[at + length + i] !== anchor.suffix[i]) break;
    score += 1;
  }
  return score;
}
