import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { useLayoutMode } from './useLayoutMode';

/** Point `window.matchMedia` at a fake, or remove it entirely with `undefined`. */
function setMatchMedia(factory: ((query: string) => unknown) | undefined) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: factory,
  });
}

afterEach(() => setMatchMedia(undefined));

describe('useLayoutMode', () => {
  it('reads the mode from matchMedia, and follows a change across each breakpoint', () => {
    // Listeners are held per query, as a browser holds them: a query tells
    // only its own listeners, and only when its answer changes.
    const listeners = new Map<string, Set<() => void>>();
    const maxOf = (query: string) => Number(/max-width: (\d+)px/.exec(query)![1]);
    let width = 390;
    setMatchMedia((query) => {
      const own = listeners.get(query) ?? new Set<() => void>();
      listeners.set(query, own);
      return {
        get matches() {
          return width <= maxOf(query);
        },
        addEventListener: (_: string, fn: () => void) => own.add(fn),
        removeEventListener: (_: string, fn: () => void) => own.delete(fn),
      };
    });
    const { result } = renderHook(() => useLayoutMode());
    expect(result.current).toBe('narrow');

    const resize = (to: number) =>
      act(() => {
        const from = width;
        width = to;
        for (const [query, own] of listeners) {
          if (from <= maxOf(query) !== to <= maxOf(query)) for (const fn of own) fn();
        }
      });
    resize(839);
    expect(result.current).toBe('narrow');
    resize(840);
    expect(result.current).toBe('medium');
    resize(1599);
    expect(result.current).toBe('medium');
    resize(1600);
    expect(result.current).toBe('wide');
  });

  it('reads wide where there is no matchMedia at all', () => {
    setMatchMedia(undefined);
    const { result } = renderHook(() => useLayoutMode());
    expect(result.current).toBe('wide');
  });
});
