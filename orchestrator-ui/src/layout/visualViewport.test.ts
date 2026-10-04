import { describe, expect, it } from 'vitest';
import { trackVisualViewport } from './visualViewport';

/** A visual viewport the test can resize, scroll and zoom, as a soft keyboard and a pinch do. */
function fakeViewport(height: number, offsetTop = 0) {
  const target = new EventTarget();
  return Object.assign(target, { height, offsetTop, scale: 1 });
}

const vars = (root: HTMLElement) => ({
  vvh: root.style.getPropertyValue('--vvh'),
  vvt: root.style.getPropertyValue('--vvt'),
});

describe('trackVisualViewport', () => {
  it('writes the visible height and top, and follows the keyboard', () => {
    const root = document.createElement('div');
    const viewport = fakeViewport(844);
    const stop = trackVisualViewport(root, viewport);
    expect(vars(root)).toEqual({ vvh: '844px', vvt: '0px' });

    viewport.height = 508;
    viewport.dispatchEvent(new Event('resize'));
    expect(vars(root)).toEqual({ vvh: '508px', vvt: '0px' });

    // iOS scrolls the layout viewport to show the focused field.
    viewport.offsetTop = 336;
    viewport.dispatchEvent(new Event('scroll'));
    expect(vars(root)).toEqual({ vvh: '508px', vvt: '336px' });

    stop();
    viewport.height = 844;
    viewport.offsetTop = 0;
    viewport.dispatchEvent(new Event('resize'));
    viewport.dispatchEvent(new Event('scroll'));
    expect(vars(root)).toEqual({ vvh: '508px', vvt: '336px' });
  });

  it('holds still while the page is pinch-zoomed, so the zoom pans over a still app', () => {
    const root = document.createElement('div');
    const viewport = fakeViewport(844);
    trackVisualViewport(root, viewport);

    Object.assign(viewport, { scale: 2, height: 422, offsetTop: 200 });
    viewport.dispatchEvent(new Event('resize'));
    viewport.dispatchEvent(new Event('scroll'));
    expect(vars(root)).toEqual({ vvh: '844px', vvt: '0px' });

    Object.assign(viewport, { scale: 1, height: 844, offsetTop: 0 });
    viewport.dispatchEvent(new Event('resize'));
    expect(vars(root)).toEqual({ vvh: '844px', vvt: '0px' });
  });

  it('leaves both unset with no visual viewport, so the CSS fallback applies', () => {
    const root = document.createElement('div');
    trackVisualViewport(root, null);
    expect(vars(root)).toEqual({ vvh: '', vvt: '' });
  });
});
