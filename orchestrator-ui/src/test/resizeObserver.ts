import { act } from 'react';
import { onTestFinished } from 'vitest';

/**
 * Replace the setup's inert `ResizeObserver` with one a test can fire, for the
 * rest of the test. The returned call reports a resize of `el` to the
 * observers watching `el` only: a stub that reports every element would pass
 * a hook that observes the wrong one.
 */
export function observeResizes(): (el: Element) => void {
  const watching = new Map<Element, Set<ResizeObserverCallback>>();
  // The setup stub is writable but not configurable, so `stubGlobal` cannot
  // replace it.
  const stub = globalThis.ResizeObserver;
  onTestFinished(() => {
    globalThis.ResizeObserver = stub;
  });
  globalThis.ResizeObserver = class {
    private readonly targets = new Set<Element>();
    private readonly callback: ResizeObserverCallback;
    constructor(callback: ResizeObserverCallback) {
      this.callback = callback;
    }
    observe(el: Element) {
      this.targets.add(el);
      if (!watching.has(el)) watching.set(el, new Set());
      watching.get(el)?.add(this.callback);
    }
    unobserve(el: Element) {
      watching.get(el)?.delete(this.callback);
    }
    disconnect() {
      for (const el of this.targets) this.unobserve(el);
    }
  } as unknown as typeof ResizeObserver;
  return (el) => {
    for (const callback of watching.get(el) ?? []) {
      act(() => callback([{ target: el } as ResizeObserverEntry], {} as ResizeObserver));
    }
  };
}
