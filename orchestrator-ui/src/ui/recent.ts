import { useCallback, useSyncExternalStore } from 'react';
import { readStoredRaw, writeStored } from './useRetained';

/** The two recent lists: comms and tasks. */
export type RecentKind = 'comm' | 'task';

/** How many ids each list keeps. A picker shows fewer. */
const KEEP = 20;

const keyOf = (kind: RecentKind) => `mael.recent.v1.${kind}`;

const listeners = new Set<() => void>();

// The last string read and the list parsed from it, per kind, so a snapshot is the same array
// until the storage changes. Keyed by the string, not cached outright: a test clears storage
// between runs, and a cache would outlive that.
const parsed = new Map<RecentKind, { raw: string | null; ids: string[] }>();

function snapshot(kind: RecentKind): string[] {
  const raw = readStoredRaw(keyOf(kind));
  const was = parsed.get(kind);
  if (was && was.raw === raw) return was.ids;
  let ids: string[] = [];
  try {
    const value: unknown = raw === null ? [] : JSON.parse(raw);
    if (Array.isArray(value)) ids = value.filter((v): v is string => typeof v === 'string');
  } catch {
    // Unreadable: start again with an empty list.
  }
  parsed.set(kind, { raw, ids });
  return ids;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => void listeners.delete(listener);
}

/**
 * The ids of `kind` this browser viewed or linked most recently, newest first, and `touch` to
 * put one at the front. Browser-local, like **Held text**: a habit of this user, not a fact
 * about the work.
 */
export function useRecent(kind: RecentKind): { ids: string[]; touch: (id: string) => void } {
  const ids = useSyncExternalStore(subscribe, () => snapshot(kind));
  const touch = useCallback(
    (id: string) => {
      const was = snapshot(kind);
      if (was[0] === id) return;
      writeStored(keyOf(kind), [id, ...was.filter((x) => x !== id)].slice(0, KEEP));
      for (const listener of listeners) listener();
    },
    [kind],
  );
  return { ids, touch };
}
