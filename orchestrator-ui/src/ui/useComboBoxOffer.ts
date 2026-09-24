import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { useAnchorName } from './useAnchorName';

/** Both duplicate `ComboBox.module.css` and `MultiComboBox.module.css` — change them together. */
const GAP = 2;
const MAX_HEIGHT = 240;

/**
 * The popover mechanics `ComboBox` and `MultiComboBox` share: open/dismiss
 * state, the anchor pair, click-outside and blur dismissal, and the
 * above/below placement math. Filtering, what a chosen row does, and the rest
 * of `onKeyDown` stay with the caller — those are where the two controls
 * differ.
 *
 * `offeredCount` drives re-placement: typing does not reopen the popover, so
 * a placement made once at open goes stale as the rows narrow under a filter.
 */
export function useComboBoxOffer(offeredCount: number) {
  const [open, setOpen] = useState(false);
  /** The row the keyboard is on, or -1 for none. Reset whenever the offer moves. */
  const [active, setActive] = useState(-1);
  const listId = useId();
  const rowId = useId();
  const box = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const { anchorStyle } = useAnchorName();

  // An empty offer shows no box: a dead end is not a reason to keep an empty
  // popover on screen.
  const showing = open && offeredCount > 0;
  const activeId = showing && active >= 0 ? `${rowId}-${active}` : undefined;

  // A click outside is a dismissal. `mousedown`, not `click`, so the box is
  // gone before the thing under the pointer takes the press.
  useEffect(() => {
    if (!showing) return;
    const onDown = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [showing]);

  // `showing` stays the one source of truth: the effect follows it, rather than
  // the popover's own open state becoming a second place to ask. That is also
  // why the popover is `manual` and not `auto` — light dismiss would close it
  // behind the component's back, and a press on a row would dismiss the offer
  // before the row's `onClick` could choose from it.
  // Which side to open, and how tall. CSS anchors the offer, but it cannot ask
  // whether the field has room below it, so JS picks the side and CSS reads the
  // choice back off `data-position`.
  const place = useCallback((el: HTMLUListElement) => {
    const field = input.current?.getBoundingClientRect();
    if (!field) return;
    const below = window.innerHeight - field.bottom - GAP;
    const above = field.top - GAP;
    // How tall the offer wants to be: its rows, capped. Measured rather than
    // assumed, because a three-row offer fits under a field that a full-height
    // one would not, and flipping that one up reads as a jump.
    el.style.removeProperty('max-height');
    const wants = Math.min(el.scrollHeight, MAX_HEIGHT);
    // Downward whenever it fits below, not merely when there is more room
    // below -- a short window often has more room above and space enough here.
    const down = wants <= below || below >= above;
    const room = down ? below : above;
    el.dataset.position = down ? 'bottom' : 'top';
    // Only cap when the side chosen has less room than the offer wants; an
    // unset max-height lets a short list draw short.
    if (room < wants) el.style.maxHeight = `${room}px`;
  }, []);

  // Open and place together, and place again whenever the offer's own height
  // moves.
  useEffect(() => {
    const el = list.current;
    if (!el) return;
    if (!showing) return;
    el.showPopover();
    place(el);
    return () => el.hidePopover();
  }, [showing, offeredCount, place]);

  return {
    open,
    setOpen,
    active,
    setActive,
    showing,
    activeId,
    listId,
    rowId,
    box,
    list,
    input,
    anchorStyle,
    place,
  };
}
