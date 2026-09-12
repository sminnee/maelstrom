import { useCallback, useEffect, useRef, useState } from 'react';
import { isStaleRetainedKey, RETAINED_PREFIX_ALL } from './retained';

/**
 * Hold what the user typed, so closing a surface does not lose it.
 *
 * `useState`'s tuple plus one verb: `[value, setValue, release]`. A caller
 * changes `useState` to `useRetained`, adds a key from `retained.ts`, and calls
 * `release()` once the work is submitted. Everything else — when to write, what
 * to do with an unreadable value, what to do without storage — is policy that
 * lives here rather than in each caller.
 *
 * `T`, not a string: new work holds its prose together with its attachments and
 * their bucket, and those have to move as one value. Three keys kept in step
 * would let a reload restore text whose image refs point at a bucket that was
 * re-minted — see `docs/dev/orchestrator-ui.md`, "Holding what was typed".
 *
 * Held text is browser-local and survives a reload. It is not view state: the
 * open tabs, the filters and the expanded node are deliberately not held.
 */

/** How long after the last keystroke the value is written. */
const WRITE_DELAY_MS = 300;

/**
 * Somewhere to hold text. `localStorage` where there is one, memory otherwise.
 *
 * Probed once, at module scope, the way `layout/useLayoutMode.ts` probes
 * `matchMedia` — Safari's private mode throws from `setItem`, and some embedded
 * views throw from the getter itself. Falling back to a `Map` means holding
 * still works across a close and fails only across a reload, with no error and
 * no banner: that is how the app behaved before any of this existed.
 */
const memory = new Map<string, string>();

/** The store the hook reads and writes, and `null` where there is none to sweep. */
const store: Storage | null = probe();

const backing: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> = store ?? {
  getItem: (key) => memory.get(key) ?? null,
  setItem: (key, value) => void memory.set(key, value),
  removeItem: (key) => void memory.delete(key),
};

function probe(): Storage | null {
  try {
    const candidate = window.localStorage;
    if (!candidate) return null;
    // Writing is what a hostile storage refuses, so probe the write rather than
    // trusting the getter.
    const canary = `${RETAINED_PREFIX_ALL}probe`;
    candidate.setItem(canary, '1');
    candidate.removeItem(canary);
    return candidate;
  } catch {
    return null;
  }
}

/** Read a held value, merged over `initial`. Anything unreadable is dropped. */
function read<T>(key: string, initial: T): T {
  let raw: string | null = null;
  try {
    raw = backing.getItem(key);
  } catch {
    return initial;
  }
  if (raw === null) return initial;
  try {
    const parsed: unknown = JSON.parse(raw);
    // Merged over `initial`, so a value written before a field existed restores
    // that field from `initial` rather than reading as `undefined`. An object
    // only: a held primitive has no fields to merge.
    if (parsed && typeof parsed === 'object' && initial && typeof initial === 'object') {
      return { ...initial, ...(parsed as object) };
    }
    return parsed as T;
  } catch {
    // Not JSON at all: the key is unusable, so it goes rather than failing
    // every mount from here on.
    forget(key);
    return initial;
  }
}

function write(key: string, value: unknown) {
  try {
    backing.setItem(key, JSON.stringify(value));
  } catch {
    // Over quota, or a storage that refuses this particular write. Drop the key
    // and carry on: a value too large to store still works in memory.
    forget(key);
  }
}

function forget(key: string) {
  try {
    backing.removeItem(key);
  } catch {
    // Nothing to do: the value is already unreachable.
  }
}

/**
 * Remove keys left by an older version, once per page.
 *
 * Lazy rather than a migration: bumping the version in `retained.ts` makes
 * every older key invisible, and this is what stops them accumulating.
 *
 * Read through `length` and `key(i)` rather than `Object.keys`, which is the
 * interface every `Storage` implements — an index-property enumeration is a
 * convenience of the browser's own object and not part of the contract.
 */
let swept = false;
function sweep() {
  if (swept || !store) return;
  swept = true;
  try {
    const stale: string[] = [];
    for (let i = 0; i < store.length; i++) {
      const key = store.key(i);
      if (key !== null && isStaleRetainedKey(key)) stale.push(key);
    }
    for (const key of stale) store.removeItem(key);
  } catch {
    // A storage that refuses to be enumerated has nothing to sweep.
  }
}

/**
 * State that outlives its component.
 *
 * `key` of `null` holds nothing, and the hook is plain `useState` — an
 * extension point with a default, for a surface that has no identity to key by
 * yet.
 */
export function useRetained<T>(
  key: string | null,
  initial: T,
): [T, React.Dispatch<React.SetStateAction<T>>, () => void] {
  // `initial` is often a fresh object literal, so it is pinned on the first
  // render: `release()` resets to what the caller first meant, not to a later
  // identity of the same shape.
  const initialRef = useRef(initial);

  // `useState`'s initialiser runs once, so the held value is read on mount and
  // never on a re-render.
  const [value, setValue] = useState<T>(() => {
    if (key === null) return initial;
    sweep();
    return read(key, initial);
  });

  // A key change means a different surface in the same component: the panel
  // draws one `SessionTab` for whichever session is active, so switching tabs
  // re-renders it rather than mounting a new one. Without a re-read the second
  // agent would be shown the first one's held reply.
  //
  // Derived state, compared during the render that changed the key: the new
  // surface's text is on screen from the first paint. An effect would paint the
  // outgoing agent's words under the incoming agent's name first.
  const [shown, setShown] = useState(key);
  if (shown !== key) {
    setShown(key);
    // `initial` itself, not the pinned copy: reading a ref during a render is
    // what the rule forbids, and the parameter is in scope right here.
    setValue(key === null ? initial : read(key, initial));
  }

  // The write is debounced, so a keystroke does not serialise JSON on the
  // thread the transcript socket needs. Every one of these is touched only
  // inside an effect or a callback.
  const latest = useRef(value);
  const keyRef = useRef(key);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Set by `release()` and cleared by the next real edit. Without it the reset
  // to `initial` reads as an ordinary value change and writes the empty value
  // straight back over the one just released.
  const releasedValue = useRef<T | null>(null);

  useEffect(() => {
    // The surface being left, if this run is a key change. Flushed for the
    // reason the unmount cleanup flushes: a tab switch inside the debounce is a
    // close as far as that text is concerned, and the only chance to write it.
    // Without this, switching away within 300 ms of a keystroke loses the reply.
    const leaving = keyRef.current;
    if (leaving !== key && timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
      if (leaving !== null) write(leaving, latest.current);
    }
    latest.current = value;
    keyRef.current = key;
    if (key === null) return;
    // The value `release()` just reset to. Not a change the user made, so it
    // must not be written.
    if (releasedValue.current !== null && value === releasedValue.current) return;
    releasedValue.current = null;
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      timer.current = null;
      write(key, latest.current);
    }, WRITE_DELAY_MS);
  }, [key, value]);

  // Flush on unmount. This is what makes a close safe whatever is mid-flight:
  // every caller unmounts its surface rather than hiding it, and React runs
  // this cleanup synchronously as it goes.
  useEffect(() => {
    return () => {
      if (timer.current === null) return;
      clearTimeout(timer.current);
      timer.current = null;
      const at = keyRef.current;
      if (at !== null) write(at, latest.current);
    };
  }, []);

  /**
   * The work was submitted: drop the held value and reset the field.
   *
   * One operation, not a `submitted` flag, because a re-render after a submit
   * would otherwise rewrite what was just released.
   */
  const release = useCallback(() => {
    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    const at = keyRef.current;
    if (at !== null) forget(at);
    const fresh = initialRef.current;
    releasedValue.current = fresh;
    latest.current = fresh;
    setValue(fresh);
  }, []);

  return [value, setValue, release];
}
