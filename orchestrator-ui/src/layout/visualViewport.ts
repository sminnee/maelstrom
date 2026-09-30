/**
 * Write the visual viewport's height to `--vvh` on `root`, and keep it current.
 *
 * A soft keyboard shrinks the visual viewport. It does not shrink `dvh` on iOS
 * Safari, or on Chrome for Android by default. CSS reads
 * `var(--vvh, 100dvh)`, so a browser with no visual viewport falls back.
 *
 * Returns a function that stops the tracking.
 */
export function trackVisualViewport(
  root: HTMLElement,
  viewport: (EventTarget & { height: number }) | null,
): () => void {
  if (!viewport) return () => {};
  const write = () => root.style.setProperty('--vvh', `${viewport.height}px`);
  write();
  viewport.addEventListener('resize', write);
  return () => viewport.removeEventListener('resize', write);
}
