import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

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
