/**
 * Write the visual viewport's height to `--vvh` and its top to `--vvt` on
 * `root`, and keep them current.
 *
 * A soft keyboard shrinks the visual viewport. It does not shrink `dvh` on iOS
 * Safari, or on Chrome for Android by default. iOS also scrolls the layout
 * viewport to show the focused field, which moves the visual viewport's top.
 * CSS reads `var(--vvh, 100dvh)` and `var(--vvt, 0)`, so a browser with no
 * visual viewport falls back.
 *
 * A pinch zoom also moves and shrinks the visual viewport. The values hold
 * while the page is zoomed, so the zoom pans over a still app rather than the
 * app following the thumb.
 *
 * Returns a function that stops the tracking.
 */
export function trackVisualViewport(
  root: HTMLElement,
  viewport: (EventTarget & { height: number; offsetTop: number; scale: number }) | null,
): () => void {
  if (!viewport) return () => {};
  const write = () => {
    if (viewport.scale !== 1) return;
    root.style.setProperty('--vvh', `${viewport.height}px`);
    root.style.setProperty('--vvt', `${viewport.offsetTop}px`);
  };
  write();
  viewport.addEventListener('resize', write);
  viewport.addEventListener('scroll', write);
  return () => {
    viewport.removeEventListener('resize', write);
    viewport.removeEventListener('scroll', write);
  };
}
