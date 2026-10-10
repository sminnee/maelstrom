/**
 * Write the visual viewport's height to `--vvh` and its top to `--vvt` on
 * `root`, and keep them current.
 *
 * A soft keyboard shrinks the visual viewport, not `dvh` or the layout
 * viewport. CSS reads `var(--vvh, 100dvh)` and `var(--vvt, 0px)`, so a
 * browser with no visual viewport falls back. See DESIGN.md, "The Still
 * Screen Rule".
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
  const write = () => {
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
  return () => {
    viewport.removeEventListener('resize', schedule);
    viewport.removeEventListener('scroll', schedule);
    if (frame) cancelAnimationFrame(frame);
  };
}
