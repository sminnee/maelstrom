import { useId, useState, type KeyboardEvent, type MouseEvent } from 'react';
import { useClamped } from './useClamped';

/**
 * A clamped body that is its own expand control.
 *
 * A `<button>` wrapper is invalid here: the body can carry a link, and a
 * button containing a link is a keyboard trap. So the collapsed body carries
 * its own `role="button"` and keydown handler instead of a separate control —
 * click and Enter/Space both expand it, and both step aside for anything
 * already interactive. Once expanded there is nothing left to expand, so the
 * body's button semantics drop and a caller-rendered "Show less" link folds
 * it back via `collapse`.
 */
export function useExpandableClamp(deps: unknown[]) {
  const [expanded, setExpanded] = useState(false);
  const bodyId = useId();
  // `expanded` toggles the caller's max-height, which changes the rendered
  // height the clamp measures — re-measure on that transition too, not only
  // on the caller's own deps.
  const [ref, clamped] = useClamped([...deps, expanded]);

  // A descendant already interactive (a link, say) steps aside from both
  // handlers. The body carries its own `role="button"` while collapsed, so
  // `closest()` cannot be used directly: called from any descendant it walks
  // past the descendant and matches that ancestor role too. Stop the walk at
  // the body itself instead of asking it to look past it.
  const hasInteractiveDescendant = (e: MouseEvent | KeyboardEvent) => {
    const body = e.currentTarget;
    for (let el = e.target as HTMLElement | null; el && el !== body; el = el.parentElement) {
      if (el.matches('a, button, input, [role="button"]')) return true;
    }
    return false;
  };

  // Nothing cut off means nothing to expand, so no "Show less" follows.
  const onBodyClick = (e: MouseEvent) => {
    if (expanded || !clamped) return;
    if (hasInteractiveDescendant(e)) return;
    setExpanded(true);
  };

  const onBodyKeyDown = (e: KeyboardEvent) => {
    if (expanded || !clamped) return;
    if (hasInteractiveDescendant(e)) return;
    if (e.key !== 'Enter' && e.key !== ' ') return;
    if (e.key === ' ') e.preventDefault();
    setExpanded(true);
  };

  return {
    expanded,
    clamped,
    collapse: () => setExpanded(false),
    bodyProps: {
      ref,
      id: bodyId,
      'data-expanded': expanded || undefined,
      role: !expanded && clamped ? ('button' as const) : undefined,
      tabIndex: !expanded && clamped ? 0 : undefined,
      'aria-expanded': !expanded && clamped ? false : undefined,
      onClick: onBodyClick,
      onKeyDown: onBodyKeyDown,
    },
  };
}
