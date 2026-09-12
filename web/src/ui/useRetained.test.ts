import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useRetained } from './useRetained';

const KEY = 'mael.retained.v1.test';

/** What storage holds under `key`, parsed. `undefined` where there is nothing. */
const held = (key = KEY) => {
  const raw = localStorage.getItem(key);
  return raw === null ? undefined : JSON.parse(raw);
};

describe('useRetained', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  /** Let the debounce fall due. */
  const settle = () => act(() => void vi.advanceTimersByTime(300));

  it('holds what was typed across an unmount, and restores it on the next mount', () => {
    const first = renderHook(() => useRetained(KEY, ''));
    act(() => first.result.current[1]('the export drops a row'));
    settle();
    // The surface closes: every caller unmounts rather than hiding.
    first.unmount();

    const second = renderHook(() => useRetained(KEY, ''));
    expect(second.result.current[0]).toBe('the export drops a row');
  });

  it('writes on a trailing edge, not on the keystroke', () => {
    // The debounce is user-visible behaviour, not an implementation detail: a
    // write per keystroke serialises JSON on the thread the transcript socket
    // needs. So the test waits for it rather than deleting it.
    const { result } = renderHook(() => useRetained(KEY, ''));
    act(() => result.current[1]('a'));
    expect(held()).toBeUndefined();
    settle();
    expect(held()).toBe('a');
  });

  it('flushes a mid-flight write when the surface closes inside the debounce', () => {
    // The case the debounce would otherwise lose: a user types a word and
    // clicks the backdrop before it falls due.
    const { result, unmount } = renderHook(() => useRetained(KEY, ''));
    act(() => result.current[1]('half a sentence'));
    expect(held()).toBeUndefined();
    unmount();
    expect(held()).toBe('half a sentence');
  });

  it('takes functional updates, so a caller can append to what is there', () => {
    const { result } = renderHook(() => useRetained(KEY, 'one'));
    act(() => result.current[1]((was) => `${was} two`));
    expect(result.current[0]).toBe('one two');
  });

  it('clears the held value and resets the field when the work is submitted', () => {
    const { result } = renderHook(() => useRetained(KEY, ''));
    act(() => result.current[1]('sent'));
    settle();
    expect(held()).toBe('sent');

    act(() => result.current[2]());
    // Both, as one operation: a re-render after a submit must not rewrite what
    // was just released.
    expect(result.current[0]).toBe('');
    expect(held()).toBeUndefined();
    settle();
    expect(held()).toBeUndefined();
  });

  it('holds again after a release, because the next keystroke is new work', () => {
    const { result } = renderHook(() => useRetained(KEY, ''));
    act(() => result.current[1]('first'));
    settle();
    act(() => result.current[2]());

    act(() => result.current[1]('second'));
    settle();
    expect(held()).toBe('second');
  });

  it('shows the value of the key it is given, and holds each key apart', () => {
    // One component, two surfaces: the panel re-renders a single `SessionTab`
    // for whichever session is active rather than mounting a new one, so a key
    // change has to re-read rather than keep what is on screen.
    const a = 'mael.retained.v1.message.a';
    const b = 'mael.retained.v1.message.b';
    const { result, rerender } = renderHook(({ key }) => useRetained(key, ''), {
      initialProps: { key: a },
    });
    act(() => result.current[1]('for a'));
    settle();

    rerender({ key: b });
    expect(result.current[0]).toBe('');
    act(() => result.current[1]('for b'));
    settle();

    rerender({ key: a });
    expect(result.current[0]).toBe('for a');
    expect(held(b)).toBe('for b');
  });

  it('flushes the surface it is leaving when the key changes inside the debounce', () => {
    // A tab switch is a close as far as the text being left is concerned, and
    // the only chance to write it: the component is re-rendered, not unmounted,
    // so the unmount flush never runs.
    const a = 'mael.retained.v1.message.a';
    const b = 'mael.retained.v1.message.b';
    const { result, rerender } = renderHook(({ key }) => useRetained(key, ''), {
      initialProps: { key: a },
    });
    act(() => result.current[1]('typed then switched away'));
    expect(held(a)).toBeUndefined();

    rerender({ key: b });
    expect(held(a)).toBe('typed then switched away');

    rerender({ key: a });
    expect(result.current[0]).toBe('typed then switched away');
  });

  it('holds a field the user emptied by hand, rather than the words it held before', () => {
    // Deleting back to an empty field is an edit like any other: the field is
    // empty on screen, so storage must be empty too. The release guard keys on
    // the value it reset to, so this pins that a later edit disarms it -- or a
    // user who cleared the field would find the words back on the next mount.
    const { result } = renderHook(() => useRetained(KEY, ''));
    act(() => result.current[2]());
    act(() => result.current[1]('a'));
    settle();
    expect(held()).toBe('a');

    act(() => result.current[1](''));
    settle();
    expect(held()).toBe('');
  });

  it('holds nothing at all for a null key', () => {
    const { result, unmount } = renderHook(() => useRetained(null, ''));
    act(() => result.current[1]('not held'));
    settle();
    expect(localStorage.length).toBe(0);
    unmount();
    expect(localStorage.length).toBe(0);

    const next = renderHook(() => useRetained(null, ''));
    expect(next.result.current[0]).toBe('');
  });

  it('reads an unreadable value as the initial one, and drops the key', () => {
    localStorage.setItem(KEY, 'not json');
    const { result } = renderHook(() => useRetained(KEY, 'fresh'));
    expect(result.current[0]).toBe('fresh');
    // Dropped rather than left to fail every mount from here on.
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('takes a missing field from the initial value, so adding one is not breaking', () => {
    // A value written by a version that had no `bucket` yet.
    localStorage.setItem(KEY, JSON.stringify({ text: 'held' }));
    const { result } = renderHook(() => useRetained(KEY, { text: '', bucket: 'draft-1' }));
    expect(result.current[0]).toEqual({ text: 'held', bucket: 'draft-1' });
  });

  it('still holds its value in memory when storage refuses the write', () => {
    // Safari's private mode throws from `setItem`. The field must keep working
    // and raise nothing: that is how the app behaved before text was held.
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError');
    });
    try {
      const { result } = renderHook(() => useRetained(KEY, ''));
      act(() => result.current[1]('typed anyway'));
      expect(() => settle()).not.toThrow();
      expect(result.current[0]).toBe('typed anyway');
    } finally {
      setItem.mockRestore();
    }
  });

  it('sweeps a key left by an older version', async () => {
    localStorage.setItem('mael.retained.v0.new-work', '"stale"');
    // A key of the current version, held by another surface, to prove the sweep
    // takes only the old ones.
    localStorage.setItem('mael.retained.v1.message.d9a4c7f1', '"live"');
    // The sweep runs once per page, which a module-level flag models -- so this
    // test needs its own instance of the module. Reaching for the shared import
    // would have it sweep on whichever test mounted first, and pass or fail on
    // the order the file happens to run in.
    vi.resetModules();
    const fresh = await import('./useRetained');
    renderHook(() => fresh.useRetained(KEY, ''));
    expect(localStorage.getItem('mael.retained.v0.new-work')).toBeNull();
    // Not collateral: another surface's current-version text stays.
    expect(localStorage.getItem('mael.retained.v1.message.d9a4c7f1')).toBe('"live"');
  });
});
