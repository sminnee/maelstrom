import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { NARROW_QUERY, useLayoutMode } from './useLayoutMode';

/** Point `window.matchMedia` at a fake, or remove it entirely with `undefined`. */
function setMatchMedia(factory: ((query: string) => unknown) | undefined) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: factory,
  });
}

afterEach(() => setMatchMedia(undefined));

describe('the breakpoint', () => {
  it('breaks at 839px, so 840 is the narrowest wide viewport', () => {
    expect(NARROW_QUERY).toBe('(max-width: 839px)');
  });
});

describe('useLayoutMode', () => {
  it('reads the mode from matchMedia, and follows a change', () => {
    const listeners = new Set<() => void>();
    let narrow = true;
    setMatchMedia((query) => {
      expect(query).toBe(NARROW_QUERY);
      return {
        get matches() {
          return narrow;
        },
        addEventListener: (_: string, fn: () => void) => listeners.add(fn),
        removeEventListener: (_: string, fn: () => void) => listeners.delete(fn),
      };
    });
    const { result } = renderHook(() => useLayoutMode());
    expect(result.current).toBe('narrow');

    act(() => {
      narrow = false;
      for (const fn of listeners) fn();
    });
    expect(result.current).toBe('wide');
  });

  it('reads wide where there is no matchMedia at all', () => {
    setMatchMedia(undefined);
    const { result } = renderHook(() => useLayoutMode());
    expect(result.current).toBe('wide');
  });
});
