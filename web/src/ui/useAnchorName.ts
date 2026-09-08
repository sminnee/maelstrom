import { useId, type CSSProperties } from 'react';

/**
 * Pair a popover with its trigger, for CSS anchor positioning.
 *
 * Returns the anchor name and a style to spread on **both** the trigger and the popover. The
 * stylesheet then reads it twice: `anchor-name: var(--anchor-name)` on the trigger, and
 * `position-anchor: var(--anchor-name)` on the popover — the latter via
 * `composes: anchored from './anchoredPopover.module.css'`, which also makes it `position: fixed`.
 *
 * The name is per-instance because anchor names are document-global. A name written into the
 * stylesheet would bind every popover on the page to whichever trigger rendered last.
 *
 * The anchor is explicit, not the implicit one a `popovertarget` gives: that association stops
 * resolving once an ancestor creates a containing block, which a transform does. `anchor()` then
 * computes to zero and the popover lands at the viewport origin. The canvas is transformed, so
 * this is not hypothetical here.
 *
 * `useId` supplies the uniqueness, but its output is not always a valid CSS identifier: React 19
 * emits `_r_<base32>_`, which is, and React 18 emitted `:r0:`, which is not. The strip guards that
 * difference, so an upgrade cannot quietly produce a name the browser drops.
 */
export function useAnchorName(): { anchorName: string; anchorStyle: CSSProperties } {
  const anchorName = `--el-${useId().replaceAll(':', '')}`;
  return { anchorName, anchorStyle: { '--anchor-name': anchorName } as CSSProperties };
}
