import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup, configure } from '@testing-library/react';
import { notifyManager } from '@tanstack/react-query';

// `findBy*` and `waitFor` have their own budget, which `testTimeout` does not
// reach. The default 1 s is shorter than a contended two-core runner takes to
// deliver a transcript, so a wait failed there while the test as a whole had
// 30 s left — a different test each run. Well under `testTimeout`, so a test
// that waits twice still reports as the wait that hung rather than as a
// blanket timeout.
configure({ asyncUtilTimeout: 5_000 });

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
const DEFAULT_VIEWPORT = 1440;
let viewportWidth: number = DEFAULT_VIEWPORT;

/**
 * Point the test viewport at a width. `renderApp` calls this.
 *
 * Moves `innerWidth` as well as the stubbed queries, because the app reads
 * both: the layout mode comes through `matchMedia`, and the panel's opening
 * width off `window.innerWidth`. One seam for both, or a test asking for a
 * narrow viewport would get a panel sized for a wide one.
 */
export function setViewportWidth(width: number) {
  viewportWidth = width;
  Object.defineProperty(window, 'innerWidth', { value: width, configurable: true });
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

// jsdom implements neither the Popover API nor `<dialog>`'s modal methods, which ComboBox and
// Dialog both call.
//
// The popover stubs cannot be bare no-ops. jsdom's UA stylesheet gives `[popover]` `display:
// none`, and a real `showPopover()` is what lifts it; with a no-op the element stays hidden, so
// `getByRole('listbox')` cannot see the offer and every test that reads it fails. So the stubs
// toggle inline `display` to model what the browser does, and fire `beforetoggle` so a component
// that positions itself on open still runs.
if (typeof HTMLElement !== 'undefined') {
  // Which popovers are open. Held here rather than read back off inline
  // `display`, because a popover that was never shown has no inline style --
  // so inferring from it would read as open and inverts the first toggle.
  const shown = new WeakSet<HTMLElement>();
  const toggle = (el: HTMLElement, open: boolean) => {
    const before = new Event('beforetoggle') as Event & { newState: string };
    before.newState = open ? 'open' : 'closed';
    el.dispatchEvent(before);
    el.style.display = open ? 'block' : 'none';
    if (open) shown.add(el);
    else shown.delete(el);
    // The browser fires `toggle` after the state settles. Components mostly
    // listen for `beforetoggle`, but one that takes `toggle` -- to measure
    // itself once it has a size -- would get silence without this.
    const after = new Event('toggle') as Event & { newState: string };
    after.newState = open ? 'open' : 'closed';
    el.dispatchEvent(after);
  };
  HTMLElement.prototype.showPopover ??= function (this: HTMLElement) {
    toggle(this, true);
  };
  HTMLElement.prototype.hidePopover ??= function (this: HTMLElement) {
    toggle(this, false);
  };
  HTMLElement.prototype.togglePopover ??= function (this: HTMLElement) {
    const open = !shown.has(this);
    toggle(this, open);
    return open;
  };
}
if (typeof HTMLDialogElement !== 'undefined') {
  HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) {
    this.open = true;
    // A modal dialog takes Escape and raises `cancel`, which is how `Dialog`
    // hears the key. jsdom implements none of that, so without this the Escape
    // route out of every dialog would go untested.
    //
    // Capture phase, on the document. Two reasons, and both decide what the
    // tests can prove. React attaches its listeners at the root container,
    // outside the dialog, so a bubble-phase listener here would run first and
    // could not see a `preventDefault()` from a control inside. And a browser
    // decides Escape from `defaultPrevented` alone -- `stopPropagation()` does
    // not stop it, because the close-request is not a listener on the way up.
    // Capturing first, then re-reading the flag once React has run, models
    // that: a control must call `preventDefault()` to hold the dialog open,
    // and a test cannot pass on `stopPropagation()` instead.
    const onKey = (e: Event) => {
      if ((e as KeyboardEvent).key !== 'Escape') return;
      // No containment check: a modal dialog takes Escape wherever the focus
      // sits, and after a button inside it unmounts the focus is on the body.
      if (!this.open) return;
      queueMicrotask(() => {
        if (e.defaultPrevented || !this.open) return;
        const cancel = new Event('cancel', { cancelable: true });
        if (this.dispatchEvent(cancel)) this.close();
      });
    };
    document.addEventListener('keydown', onKey, true);
    this.addEventListener('close', () => document.removeEventListener('keydown', onKey, true));
  };
  HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) {
    this.open = false;
    this.dispatchEvent(new Event('close'));
  };
}

afterEach(() => {
  // Before `cleanup`, which unmounts the tree the dialogs live in. A dialog
  // left open holds a capture-phase keydown listener on the document, and
  // `close` is what removes it -- unmounting alone would strand it.
  for (const dialog of document.querySelectorAll('dialog[open]')) {
    (dialog as HTMLDialogElement).close();
  }
  cleanup();
  // The stubs above are deterministic and need no restoring. These two carry
  // state between tests, so a test that left either moved would reach the
  // next one. `renderApp` resets the store and builds a new QueryClient, so
  // the app's own state needs nothing here.
  viewportWidth = DEFAULT_VIEWPORT;
  mediaListeners.clear();
});
