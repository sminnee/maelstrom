import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { trackVisualViewport } from './visualViewport';

/** A visual viewport the test can resize, pan and zoom, as a soft keyboard and a pinch do. */
function fakeViewport(height: number) {
  const target = new EventTarget();
  return Object.assign(target, { height, offsetTop: 0, scale: 1 });
}

const vvh = (root: HTMLElement) => root.style.getPropertyValue('--vvh');
const vvt = (root: HTMLElement) => root.style.getPropertyValue('--vvt');

/** Fire `resize` with the viewport at `height`, and let the frame run. */
function resize(viewport: ReturnType<typeof fakeViewport>, height: number) {
  viewport.height = height;
  viewport.dispatchEvent(new Event('resize'));
}
const nextFrame = () => vi.advanceTimersToNextFrame();

/** An element in the page, a textarea unless named. */
function field(tag = 'textarea', attrs: Record<string, string> = {}) {
  const el = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) el.setAttribute(name, value);
  document.body.append(el);
  return el;
}

// The tracker listens on the shared document, so each test stops its own.
const stops: Array<() => void> = [];

/** Track an 844px viewport, with `focused` focused first. */
function track(focused?: HTMLElement) {
  focused?.focus();
  const root = document.createElement('div');
  const viewport = fakeViewport(844);
  const stop = trackVisualViewport(root, viewport);
  stops.push(stop);
  return { root, viewport, stop };
}

/** Track while a textarea has the focus, so the keyboard is open. */
const editing = () => track(field());

describe('trackVisualViewport', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame'] });
  });
  afterEach(() => {
    stops.splice(0).forEach((stop) => stop());
    vi.useRealTimers();
    document.body.replaceChildren();
  });

  it('writes the visible top and height while a field has the focus, and follows the keyboard', () => {
    const { root, viewport, stop } = editing();
    expect(vvh(root)).toBe('844px');
    expect(vvt(root)).toBe('0px');

    resize(viewport, 508);
    nextFrame();
    expect(vvh(root)).toBe('508px');

    // iOS pans the visible area over the page to reach a field.
    viewport.offsetTop = 120;
    viewport.dispatchEvent(new Event('scroll'));
    nextFrame();
    expect(vvt(root)).toBe('120px');

    stop();
    resize(viewport, 844);
    nextFrame();
    expect(vvh(root)).toBe('508px');
  });

  it('writes once for every event in one frame, with the last height', () => {
    const { root, viewport } = editing();
    const set = vi.spyOn(root.style, 'setProperty');

    resize(viewport, 700);
    viewport.dispatchEvent(new Event('scroll'));
    resize(viewport, 508);
    expect(set).not.toHaveBeenCalled();
    nextFrame();
    // One write of each, with the last height.
    expect(set).toHaveBeenCalledTimes(2);
    expect(vvh(root)).toBe('508px');
    expect(vvt(root)).toBe('0px');
  });

  it('writes whole pixels, as iOS reports a fraction', () => {
    const { root, viewport } = editing();

    viewport.offsetTop = 119.6;
    resize(viewport, 507.6);
    nextFrame();
    expect(vvh(root)).toBe('508px');
    expect(vvt(root)).toBe('120px');
  });

  it('drops a write still waiting for its frame when it stops', () => {
    const { root, viewport, stop } = editing();

    resize(viewport, 508);
    stop();
    nextFrame();
    expect(vvh(root)).toBe('844px');
  });

  it('ignores a height of 0, which iOS reports for a moment', () => {
    const { root, viewport } = editing();

    resize(viewport, 0);
    nextFrame();
    expect(vvh(root)).toBe('844px');
  });

  it('holds still while the page is pinch-zoomed, so the zoom pans over a still app', () => {
    const { root, viewport } = editing();

    viewport.scale = 2;
    resize(viewport, 422);
    nextFrame();
    expect(vvh(root)).toBe('844px');

    viewport.scale = 1;
    resize(viewport, 844);
    nextFrame();
    expect(vvh(root)).toBe('844px');
  });

  it('clears them when the field loses the focus, with no further viewport event', () => {
    const notes = field();
    const { root, viewport } = track(notes);
    resize(viewport, 508);
    nextFrame();

    notes.blur();
    nextFrame();
    expect(vvh(root)).toBe('');
    expect(vvt(root)).toBe('');
  });

  it('keeps them when the focus moves from one field to the next in one frame', () => {
    const title = field();
    const notes = field();
    const { root, viewport } = track(title);
    resize(viewport, 508);
    nextFrame();

    title.blur();
    // The check waits for the frame, so the gap between the two fields does not clear them.
    expect(vvh(root)).toBe('508px');
    notes.focus();
    nextFrame();
    expect(vvh(root)).toBe('508px');
  });

  it('leaves them unset while nothing has the focus, so the CSS fallback applies', () => {
    const { root, viewport } = track();
    expect(vvh(root)).toBe('');

    resize(viewport, 508);
    nextFrame();
    expect(vvh(root)).toBe('');
    expect(vvt(root)).toBe('');
  });

  it('leaves them unset while a control that takes no text has the focus', () => {
    const { root, viewport } = track(field('input', { type: 'checkbox' }));
    resize(viewport, 508);
    nextFrame();
    expect(vvh(root)).toBe('');
  });

  it('follows the viewport while a frame has the focus, as it cannot see the field inside', () => {
    const { root, viewport } = track(field('iframe'));
    resize(viewport, 508);
    nextFrame();
    expect(vvh(root)).toBe('508px');
  });

  it('leaves it unset with no visual viewport, so the CSS fallback applies', () => {
    const root = document.createElement('div');
    trackVisualViewport(root, null);
    expect(vvh(root)).toBe('');
    expect(vvt(root)).toBe('');
  });
});
