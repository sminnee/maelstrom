import { describe, expect, it } from 'vitest';
import { trackVisualViewport } from './visualViewport';

/** A visual viewport the test can resize, as a soft keyboard does. */
function fakeViewport(height: number) {
  const target = new EventTarget();
  return Object.assign(target, { height });
}

describe('trackVisualViewport', () => {
  it('writes the visible height to --vvh, and follows the keyboard', () => {
    const root = document.createElement('div');
    const viewport = fakeViewport(844);
    const stop = trackVisualViewport(root, viewport);
    expect(root.style.getPropertyValue('--vvh')).toBe('844px');

    viewport.height = 508;
    viewport.dispatchEvent(new Event('resize'));
    expect(root.style.getPropertyValue('--vvh')).toBe('508px');

    stop();
    viewport.height = 844;
    viewport.dispatchEvent(new Event('resize'));
    expect(root.style.getPropertyValue('--vvh')).toBe('508px');
  });

  it('leaves --vvh unset with no visual viewport, so the CSS fallback applies', () => {
    const root = document.createElement('div');
    trackVisualViewport(root, null);
    expect(root.style.getPropertyValue('--vvh')).toBe('');
  });
});
