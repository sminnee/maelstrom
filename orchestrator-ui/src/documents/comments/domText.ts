import type { Span } from './anchor';

/**
 * A DOM Range as offsets into `container.textContent`. Walks the text nodes
 * in document order, so a selection across elements maps to one span.
 */
export function rangeToTextOffsets(container: Element, range: Range): Span {
  let offset = 0;
  let start = -1;
  let end = -1;
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const length = node.textContent?.length ?? 0;
    if (node === range.startContainer) start = offset + range.startOffset;
    if (node === range.endContainer) end = offset + range.endOffset;
    if (start === -1 && isBefore(range.startContainer, range.startOffset, node)) start = offset;
    if (end === -1 && isBefore(range.endContainer, range.endOffset, node)) end = offset;
    offset += length;
  }
  if (start === -1) start = range.startContainer === container ? 0 : offset;
  if (end === -1) end = offset;
  return { start: Math.min(start, end), end: Math.max(start, end) };
}

/** True when the boundary point (container, offset) is before `node` starts. */
function isBefore(boundary: Node, boundaryOffset: number, node: Node): boolean {
  if (boundary.nodeType === Node.TEXT_NODE) return false;
  const point = document.createRange();
  point.setStart(boundary, boundaryOffset);
  point.collapse(true);
  return point.comparePoint(node, 0) >= 0;
}
