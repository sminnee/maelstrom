import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { act, cleanup, configure } from '@testing-library/react';
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

// A data router gives each navigation's `Request` an `AbortSignal`, and Node's `Request`
// refuses jsdom's. The app runs no loaders, so nothing waits on the signal: drop it.
class RequestWithoutSignal extends Request {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    super(input, { ...init, signal: undefined });
  }
}
Object.defineProperty(globalThis, 'Request', { writable: true, value: RequestWithoutSignal });

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

// jsdom has no matchMedia. The app reads the layout queries through it, and
// `prefers-reduced-motion` in the node card. The stub answers them all from
// one settable viewport: a width, a height and a pointer kind.
type Viewport = { width: number; height: number; pointer: 'fine' | 'coarse' | 'none' };
const DEFAULT_VIEWPORT: Viewport = { width: 1920, height: 1080, pointer: 'fine' };
let viewport: Viewport = DEFAULT_VIEWPORT;

/**
 * Point the test viewport at a width, with the default height and pointer.
 * `renderApp` calls this.
 */
export function setViewportWidth(width: number) {
  setViewport({ ...DEFAULT_VIEWPORT, width });
}

/**
 * Point the test viewport at a size and a pointer kind, as a phone turned to
 * landscape or a tablet is.
 *
 * Moves `innerWidth` as well as the stubbed queries, because the app reads
 * both: the layout mode comes through `matchMedia`, and the panel's opening
 * width off `window.innerWidth`. One seam for both, or a test asking for a
 * narrow viewport would get a panel sized for a wide one.
 */
export function setViewport(to: Viewport) {
  viewport = to;
  Object.defineProperty(window, 'innerWidth', { value: to.width, configurable: true });
  Object.defineProperty(window, 'innerHeight', { value: to.height, configurable: true });
  // `resize` before `change`, as a browser runs its resize steps before it
  // evaluates media queries.
  act(() => {
    window.dispatchEvent(new Event('resize'));
  });
  // Each list in its own `act`, as a browser dispatches `change`: React renders
  // between two lists, so a tree that reads two sources of the mode tears here.
  for (const listener of [...mediaListeners]) act(() => listener());
}

/**
 * Whether the viewport matches one media feature, such as `max-width: 839px`
 * or `pointer: coarse`. A feature the stub does not know, such as
 * `prefers-reduced-motion`, reads as unset.
 */
function matchesFeature(feature: string): boolean {
  const range = /^(max|min)-(width|height):\s*(\d+)px$/.exec(feature);
  if (range) {
    const [, bound, axis, px] = range;
    const size = axis === 'width' ? viewport.width : viewport.height;
    return bound === 'max' ? size <= Number(px) : size >= Number(px);
  }
  const pointer = /^pointer:\s*(\w+)$/.exec(feature);
  if (pointer) return viewport.pointer === pointer[1];
  return false;
}

/**
 * Whether the viewport matches a media query list: any of its comma-separated
 * queries. Each query is a condition of `(feature)`s, nested parentheses and
 * `not`, `and` and `or`, as Media Queries Level 4 writes it.
 */
function matchesQuery(list: string): boolean {
  return list.split(',').some((query) => {
    // Each `(feature: value)` becomes one token, so only the grammar's own
    // parentheses are left to parse.
    const features: string[] = [];
    const text = query.replace(/\(([a-z-]+:[^()]*)\)/g, (_, feature: string) => {
      features.push(feature.trim());
      return ` f${features.length - 1} `;
    });
    const tokens = text.match(/\(|\)|f\d+|\bnot\b|\band\b|\bor\b/g) ?? [];
    let at = 0;
    // condition = not primary | primary ((and | or) primary)*
    function condition(): boolean {
      if (tokens[at] === 'not') {
        at++;
        return !primary();
      }
      let value = primary();
      while (tokens[at] === 'and' || tokens[at] === 'or') {
        const op = tokens[at++];
        const right = primary();
        value = op === 'and' ? value && right : value || right;
      }
      return value;
    }
    // primary = feature | ( condition )
    function primary(): boolean {
      const token = tokens[at++];
      if (token !== '(') return matchesFeature(features[Number(token?.slice(1))] ?? '');
      const value = condition();
      at++; // )
      return value;
    }
    return condition();
  });
}

const mediaListeners = new Set<() => void>();

Object.defineProperty(globalThis, 'matchMedia', {
  writable: true,
  value: (query: string) => {
    const listeners = new Set<(e: MediaQueryListEvent) => void>();
    const matches = () => matchesQuery(query);
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
    // A browser fires `change` only when the answer flips.
    let last = matches();
    function notify() {
      if (matches() === last) return;
      last = matches();
      for (const fn of listeners) fn({ matches: matches(), media: query } as MediaQueryListEvent);
    }
    return media;
  },
});

// jsdom defers `localStorage` to Node, which refuses it unless started with
// `--localstorage-file`, so the getter reads `undefined` rather than throwing.
// Held text is read through it (see `ui/useRetained.ts`), and the hook falls
// back to memory when storage is missing -- so without this every test would
// silently exercise that fallback and prove nothing about storage itself.
//
// Map-backed rather than Node's own: it needs no flag, no temp file and no
// experimental warning.
//
// The methods go on `Storage.prototype`, not on a class of their own. A test
// makes storage hostile with `vi.spyOn(Storage.prototype, 'setItem')`, and a
// stub that merely implemented the same shape would not be reached by that
// spy -- the write would quietly succeed and the test would assert nothing.
if (typeof window.localStorage === 'undefined') {
  const items = new Map<string, string>();
  Object.defineProperties(Storage.prototype, {
    length: { configurable: true, get: () => items.size },
    key: { configurable: true, value: (i: number) => [...items.keys()][i] ?? null },
    getItem: { configurable: true, value: (k: string) => items.get(String(k)) ?? null },
    // The spec stores strings, keys included, so a caller passing a number must
    // read one back -- otherwise a test passes here and fails in a browser.
    setItem: {
      configurable: true,
      value: (k: string, v: string) => void items.set(String(k), String(v)),
    },
    removeItem: { configurable: true, value: (k: string) => void items.delete(String(k)) },
    clear: { configurable: true, value: () => items.clear() },
  });
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    writable: true,
    value: Object.create(Storage.prototype) as Storage,
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
  viewport = DEFAULT_VIEWPORT;
  mediaListeners.clear();
  // Storage is a singleton, so held text one test left would reach the next one
  // and open a field already filled in, and tabs it kept would open in the
  // next one's panel. Here rather than in `renderApp`, because a component
  // test never calls it.
  localStorage.clear();
  sessionStorage.clear();
});
