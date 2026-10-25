/**
 * Write the visual viewport's height to `--vvh` and its top to `--vvt` on
 * `root` while a field has the focus, and keep them current. Clear them while
 * no field has it.
 *
 * A soft keyboard shrinks the visual viewport, not `dvh` or the layout
 * viewport. CSS reads `var(--vvh, 100dvh)` and `var(--vvt, 0px)`, so a
 * browser with no visual viewport falls back. See DESIGN.md, "The Still
 * Screen Rule".
 *
 * iOS can close the keyboard with no last viewport event, so the values clear
 * on the blur. The check waits for the frame, so a focus move between fields
 * keeps them.
 *
 * While no field and no select has the focus, it also scrolls the page back
 * to 0, except while the page is zoomed, as a zoom pans the visual viewport.
 * A select is skipped, as iOS pans to show its picker.
 *
 * Writes wait for the next frame, so a burst of events writes once. iOS reports
 * a height of 0 for a moment while the keyboard moves, and that is skipped.
 *
 * A pinch zoom also shrinks the visual viewport. The value holds while the
 * page is zoomed, so the zoom pans over a still app rather than the app
 * following the thumb.
 *
 * Returns a function that stops the tracking.
 */
export function trackVisualViewport(
  root: HTMLElement,
  viewport: (EventTarget & { height: number; offsetTop: number; scale: number }) | null,
): () => void {
  if (!viewport) return () => {};
  const doc = root.ownerDocument;
  const win = doc.defaultView;
  const write = () => {
    if (!isEditing(doc.activeElement)) {
      root.style.removeProperty('--vvh');
      root.style.removeProperty('--vvt');
      if (
        win &&
        !(doc.activeElement instanceof HTMLSelectElement) &&
        viewport.scale === 1 &&
        (viewport.offsetTop !== 0 || win.scrollY !== 0)
      )
        win.scrollTo(0, 0);
      return;
    }
    if (viewport.scale !== 1 || viewport.height <= 0) return;
    // Whole pixels: iOS reports a fraction, and a box a fraction short shows
    // a hairline of page under it.
    root.style.setProperty('--vvh', `${Math.round(viewport.height)}px`);
    root.style.setProperty('--vvt', `${Math.round(viewport.offsetTop)}px`);
  };
  let frame = 0;
  const schedule = () => {
    if (!frame)
      frame = requestAnimationFrame(() => {
        frame = 0;
        write();
      });
  };
  write();
  viewport.addEventListener('resize', schedule);
  viewport.addEventListener('scroll', schedule);
  win?.addEventListener('scroll', schedule);
  doc.addEventListener('focusin', schedule);
  doc.addEventListener('focusout', schedule);
  return () => {
    doc.removeEventListener('focusin', schedule);
    doc.removeEventListener('focusout', schedule);
    viewport.removeEventListener('resize', schedule);
    viewport.removeEventListener('scroll', schedule);
    win?.removeEventListener('scroll', schedule);
    if (frame) cancelAnimationFrame(frame);
  };
}

/** Inputs that take no typed text, so open no soft keyboard. */
const NO_TEXT = new Set([
  'button',
  'checkbox',
  'color',
  'file',
  'hidden',
  'image',
  'radio',
  'range',
  'reset',
  'submit',
]);

/**
 * Whether a soft keyboard may be open while `el` has the focus: a field that
 * takes typed text, or a frame, whose own focused field the page cannot see.
 */
function isEditing(el: Element | null): boolean {
  if (el instanceof HTMLInputElement) return !NO_TEXT.has(el.type) && !el.readOnly;
  if (el instanceof HTMLTextAreaElement) return !el.readOnly;
  return el instanceof HTMLIFrameElement || (el instanceof HTMLElement && el.isContentEditable);
}
