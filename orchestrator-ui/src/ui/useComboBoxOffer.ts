import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { useAnchorName } from './useAnchorName';

/** Both duplicate `ComboBox.module.css` and `MultiComboBox.module.css` — change them together. */
const GAP = 2;
const MAX_HEIGHT = 240;
/** Three rows. With less room than this above the field, and more below, a touch screen opens below. */
const MIN_ABOVE = 90;
/** How long a focus on a touch screen waits for a keyboard that may not come. */
const KEYBOARD_WAIT = 400;

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

  // On a touch screen a focus opens the soft keyboard, which then shrinks the
  // box and makes the dialog scroll the field into view. Until that settles,
  // a placement is for a box that is about to change, so the offer waits.
  const [settled, setSettled] = useState(true);
  useEffect(() => {
    const el = input.current;
    if (!el || !isTouchScreen()) return;
    const viewport = window.visualViewport;
    let frame = 0;
    let timer = 0;
    const settle = () => {
      viewport?.removeEventListener('resize', onResize);
      clearTimeout(timer);
      setSettled(true);
    };
    // A frame after the resize: `--vvh` is written and the field revealed.
    const onResize = () => {
      if (!frame)
        frame = requestAnimationFrame(() => {
          frame = 0;
          settle();
        });
    };
    const onFocus = () => {
      clearTimeout(timer);
      setSettled(false);
      viewport?.addEventListener('resize', onResize);
      // No resize comes when the keyboard is already up.
      timer = window.setTimeout(settle, KEYBOARD_WAIT);
    };
    el.addEventListener('focus', onFocus);
    return () => {
      el.removeEventListener('focus', onFocus);
      viewport?.removeEventListener('resize', onResize);
      clearTimeout(timer);
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  // An empty offer shows no box: a dead end is not a reason to keep an empty
  // popover on screen.
  const showing = open && settled && offeredCount > 0;
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
    // The visible area, not the window: on iOS a soft keyboard shrinks the
    // visual viewport and leaves `innerHeight` as it was.
    const top = window.visualViewport?.offsetTop ?? 0;
    const bottom = window.visualViewport ? top + window.visualViewport.height : window.innerHeight;
    const below = bottom - field.bottom - GAP;
    const above = field.top - top - GAP;
    // How tall the offer wants to be: its rows, capped. Measured rather than
    // assumed, because a three-row offer fits under a field that a full-height
    // one would not, and flipping that one up reads as a jump.
    el.style.removeProperty('max-height');
    const wants = Math.min(el.scrollHeight, MAX_HEIGHT);
    // A touch screen opens upward, away from the keyboard. A fine pointer opens
    // downward whenever the offer fits below, not only when below has more room.
    const down = isTouchScreen()
      ? above < MIN_ABOVE && below > above
      : wants <= below || below >= above;
    const room = down ? below : above;
    el.dataset.position = down ? 'bottom' : 'top';
    // Only cap when the side chosen has less room than the offer wants; an
    // unset max-height lets a short list draw short.
    if (room < wants) el.style.maxHeight = `${room}px`;
    if (!CSS.supports('top', 'anchor(bottom)')) placeByHand(el, field, down);
  }, []);

  // Open and place together, and place again whenever the offer's own height
  // moves.
  useEffect(() => {
    const el = list.current;
    if (!el) return;
    if (!showing) return;
    el.showPopover();
    place(el);
    // Place again a frame after a resize or a scroll, as the dialog reveals the
    // field. A scroll does not bubble, so the document hears it in the capture
    // phase. The offer's own scroll is not one: placing resets its height, which
    // would clamp it.
    let frame = 0;
    const replace = (e: Event) => {
      if (e.target === el || frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        place(el);
      });
    };
    const viewport = window.visualViewport;
    viewport?.addEventListener('resize', replace);
    viewport?.addEventListener('scroll', replace);
    document.addEventListener('scroll', replace, true);
    return () => {
      viewport?.removeEventListener('resize', replace);
      viewport?.removeEventListener('scroll', replace);
      document.removeEventListener('scroll', replace, true);
      if (frame) cancelAnimationFrame(frame);
      el.hidePopover();
    };
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

/**
 * Write the offsets the anchor rules would, for a browser that lays out no
 * anchors, such as iOS Safari before 26.
 *
 * The offset is a first guess, then corrected by where the offer is drawn:
 * on iOS a fixed box and a rect disagree once the keyboard is open, by an
 * amount no property reports. Two rects always agree with each other.
 */
function placeByHand(el: HTMLUListElement, field: DOMRect, down: boolean) {
  el.style.left = `${field.left}px`;
  el.style.minWidth = `${field.width}px`;
  const guess = down ? field.bottom + GAP : field.top - GAP - el.getBoundingClientRect().height;
  el.style.top = `${guess}px`;
  const drawn = el.getBoundingClientRect();
  const off = down ? drawn.top - (field.bottom + GAP) : drawn.bottom - (field.top - GAP);
  if (off) el.style.top = `${guess - off}px`;
}

/** Whether the main pointer is a finger, so a focus opens a soft keyboard. */
const isTouchScreen = () => window.matchMedia?.('(pointer: coarse)').matches ?? false;
