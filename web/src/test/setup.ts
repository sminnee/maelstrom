import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';
import { notifyManager } from '@tanstack/react-query';

// The query cache batches its notifications on a timer. Synchronous ones let
// a test read the screen right after the cache moved, inside the same act.
notifyManager.setScheduler((callback) => callback());

// React Flow measures nodes with ResizeObserver and reads transforms with
// DOMMatrixReadOnly. jsdom has neither, so both are stubbed for tests.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

class DOMMatrixReadOnlyStub {
  m22 = 1;
  constructor(transform?: string) {
    const scale = transform?.match(/scale\(([\d.]+)\)/)?.[1];
    this.m22 = scale ? Number(scale) : 1;
  }
}

Object.defineProperty(globalThis, 'ResizeObserver', { writable: true, value: ResizeObserverStub });
Object.defineProperty(globalThis, 'DOMMatrixReadOnly', {
  writable: true,
  value: DOMMatrixReadOnlyStub,
});
Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
  configurable: true,
  get() {
    return Number(this.getAttribute('height')) || 1;
  },
});
Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
  configurable: true,
  get() {
    return Number(this.getAttribute('width')) || 1;
  },
});
if (!('getBBox' in SVGElement.prototype)) {
  Object.defineProperty(SVGElement.prototype, 'getBBox', {
    writable: true,
    value: () => ({ x: 0, y: 0, width: 0, height: 0 }),
  });
}

// jsdom has no matchMedia. The app reads two queries through it: the layout
// mode, and `prefers-reduced-motion` in the node card. The stub answers both
// from one settable width.
let viewportWidth = 1440;

/** Point the stubbed matchMedia at a viewport width. `renderApp` calls this. */
export function setViewportWidth(width: number) {
  viewportWidth = width;
  for (const listener of mediaListeners) listener();
}

const mediaListeners = new Set<() => void>();

Object.defineProperty(globalThis, 'matchMedia', {
  writable: true,
  value: (query: string) => {
    const listeners = new Set<(e: MediaQueryListEvent) => void>();
    const matches = () => {
      const max = /\(max-width:\s*(\d+)px\)/.exec(query);
      if (max) return viewportWidth <= Number(max[1]);
      const min = /\(min-width:\s*(\d+)px\)/.exec(query);
      if (min) return viewportWidth >= Number(min[1]);
      // Everything else, `prefers-reduced-motion` included, reads as unset.
      return false;
    };
    const media = {
      get matches() {
        return matches();
      },
      media: query,
      onchange: null,
      addEventListener: (_: string, fn: (e: MediaQueryListEvent) => void) => {
        listeners.add(fn);
        mediaListeners.add(notify);
      },
      removeEventListener: (_: string, fn: (e: MediaQueryListEvent) => void) => {
        listeners.delete(fn);
        if (listeners.size === 0) mediaListeners.delete(notify);
      },
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    };
    function notify() {
      for (const fn of listeners) fn({ matches: matches(), media: query } as MediaQueryListEvent);
    }
    return media;
  },
});

// jsdom has no EventSource. The app injects one; this keeps an un-injected
// import from throwing before the test can say what it wants.
if (!('EventSource' in globalThis)) {
  class EventSourceStub {
    readyState = 0;
    onopen = null;
    onerror = null;
    addEventListener() {}
    close() {}
  }
  Object.defineProperty(globalThis, 'EventSource', { writable: true, value: EventSourceStub });
}

afterEach(() => cleanup());
