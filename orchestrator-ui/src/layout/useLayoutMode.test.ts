import { renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { setViewport, setViewportWidth } from '../test/setup';
import { useLayoutMode } from './useLayoutMode';

describe('useLayoutMode', () => {
  it('reads the mode from matchMedia, and follows a change across each breakpoint', () => {
    setViewportWidth(390);
    const { result } = renderHook(() => useLayoutMode());
    expect(result.current).toBe('narrow');

    setViewportWidth(839);
    expect(result.current).toBe('narrow');
    setViewportWidth(840);
    expect(result.current).toBe('medium');
    setViewportWidth(1599);
    expect(result.current).toBe('medium');
    setViewportWidth(1600);
    expect(result.current).toBe('wide');
  });

  it('reads a phone in landscape as narrow, and a short desktop window or a tablet as medium', () => {
    setViewport({ width: 932, height: 430, pointer: 'coarse' });
    const { result } = renderHook(() => useLayoutMode());
    expect(result.current).toBe('narrow');

    setViewport({ width: 932, height: 430, pointer: 'fine' });
    expect(result.current).toBe('medium');
    setViewport({ width: 1024, height: 768, pointer: 'coarse' });
    expect(result.current).toBe('medium');
  });

  it('draws the short touch screen edge at 539px high and 959px wide', () => {
    setViewport({ width: 932, height: 539, pointer: 'coarse' });
    const { result } = renderHook(() => useLayoutMode());
    expect(result.current).toBe('narrow');
    setViewport({ width: 932, height: 540, pointer: 'coarse' });
    expect(result.current).toBe('medium');
    setViewport({ width: 959, height: 430, pointer: 'coarse' });
    expect(result.current).toBe('narrow');
    setViewport({ width: 960, height: 430, pointer: 'coarse' });
    expect(result.current).toBe('medium');
  });

  it('keeps a tablet medium while a soft keyboard shortens its viewport', () => {
    // Android resizes the layout viewport for the keyboard (`interactive-widget`
    // in index.html), so a 1280x800 tablet in landscape is briefly 1280x450.
    setViewport({ width: 1280, height: 800, pointer: 'coarse' });
    const { result } = renderHook(() => useLayoutMode());
    expect(result.current).toBe('medium');
    setViewport({ width: 1280, height: 450, pointer: 'coarse' });
    expect(result.current).toBe('medium');
  });

  it('keeps a phone narrow when it turns', () => {
    setViewport({ width: 390, height: 844, pointer: 'coarse' });
    const { result } = renderHook(() => useLayoutMode());
    expect(result.current).toBe('narrow');
    setViewport({ width: 844, height: 390, pointer: 'coarse' });
    expect(result.current).toBe('narrow');
  });

  it('reads the viewport afresh once the last reader has gone', () => {
    setViewportWidth(390);
    const first = renderHook(() => useLayoutMode());
    expect(first.result.current).toBe('narrow');
    first.unmount();
    setViewportWidth(1920);
    expect(renderHook(() => useLayoutMode()).result.current).toBe('wide');
  });

  it('reads wide where there is no matchMedia at all', () => {
    const { matchMedia } = window;
    (window as { matchMedia?: unknown }).matchMedia = undefined;
    try {
      setViewportWidth(390);
      const { result } = renderHook(() => useLayoutMode());
      expect(result.current).toBe('wide');
    } finally {
      window.matchMedia = matchMedia;
    }
  });
});
