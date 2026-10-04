import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { trackVisualViewport } from './visualViewport';

/** A visual viewport the test can resize and zoom, as a soft keyboard and a pinch do. */
function fakeViewport(height: number) {
  const target = new EventTarget();
  return Object.assign(target, { height, scale: 1 });
}

const vvh = (root: HTMLElement) => root.style.getPropertyValue('--vvh');

/** Fire `resize` with the viewport at `height`, and let the frame run. */
function resize(viewport: ReturnType<typeof fakeViewport>, height: number) {
  viewport.height = height;
  viewport.dispatchEvent(new Event('resize'));
}
const nextFrame = () => vi.advanceTimersToNextFrame();

describe('trackVisualViewport', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame'] });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('writes the visible height, follows the keyboard, and writes no top', () => {
    const root = document.createElement('div');
    const viewport = fakeViewport(844);
    const stop = trackVisualViewport(root, viewport);
    // At once, so the first paint has it.
    expect(vvh(root)).toBe('844px');

    resize(viewport, 508);
    nextFrame();
    expect(vvh(root)).toBe('508px');
    // The app anchors to the bottom, so the top is not tracked.
    expect(root.style.getPropertyValue('--vvt')).toBe('');

    stop();
    resize(viewport, 844);
    nextFrame();
    expect(vvh(root)).toBe('508px');
  });

  it('writes once for every event in one frame, with the last height', () => {
    const root = document.createElement('div');
    const viewport = fakeViewport(844);
    trackVisualViewport(root, viewport);
    const set = vi.spyOn(root.style, 'setProperty');

    resize(viewport, 700);
    viewport.dispatchEvent(new Event('scroll'));
    resize(viewport, 508);
    expect(set).not.toHaveBeenCalled();
    nextFrame();
    expect(set.mock.calls).toEqual([['--vvh', '508px']]);
  });

  it('writes whole pixels, as iOS reports a fraction', () => {
    const root = document.createElement('div');
    const viewport = fakeViewport(844);
    trackVisualViewport(root, viewport);

    resize(viewport, 507.6);
    nextFrame();
    expect(vvh(root)).toBe('508px');
  });

  it('drops a write still waiting for its frame when it stops', () => {
    const root = document.createElement('div');
    const viewport = fakeViewport(844);
    const stop = trackVisualViewport(root, viewport);

    resize(viewport, 508);
    stop();
    nextFrame();
    expect(vvh(root)).toBe('844px');
  });

  it('ignores a height of 0, which iOS reports for a moment', () => {
    const root = document.createElement('div');
    const viewport = fakeViewport(844);
    trackVisualViewport(root, viewport);

    resize(viewport, 0);
    nextFrame();
    expect(vvh(root)).toBe('844px');
  });

  it('holds still while the page is pinch-zoomed, so the zoom pans over a still app', () => {
    const root = document.createElement('div');
    const viewport = fakeViewport(844);
    trackVisualViewport(root, viewport);

    viewport.scale = 2;
    resize(viewport, 422);
    nextFrame();
    expect(vvh(root)).toBe('844px');

    viewport.scale = 1;
    resize(viewport, 844);
    nextFrame();
    expect(vvh(root)).toBe('844px');
  });

  it('leaves it unset with no visual viewport, so the CSS fallback applies', () => {
    const root = document.createElement('div');
    trackVisualViewport(root, null);
    expect(vvh(root)).toBe('');
  });
});
