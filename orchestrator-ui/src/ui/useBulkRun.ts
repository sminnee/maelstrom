import { useState } from 'react';
import { messageOf } from './useClickLifecycle';

/** What a bulk run left undone, and why, as one line. */
export interface BulkResult<T> {
  failed: T[];
  /** `"N of M failed: why"`, the first refusal's words; null when none failed. */
  failure: string | null;
}

/**
 * One change applied to many targets: the run behind a **Bulk bar**. One at a
 * time, as each write re-reads the notebook and parallel re-reads race. A
 * refused target does not stop the others.
 */
export function useBulkRun() {
  const [busy, setBusy] = useState(false);
  const run = async <T>(targets: T[], send: (t: T) => Promise<unknown>): Promise<BulkResult<T>> => {
    const failed: T[] = [];
    let why = '';
    setBusy(true);
    for (const target of targets) {
      try {
        await send(target);
      } catch (err) {
        failed.push(target);
        why ||= messageOf(err);
      }
    }
    setBusy(false);
    return {
      failed,
      failure: failed.length ? `${failed.length} of ${targets.length} failed: ${why}` : null,
    };
  };
  return { busy, run };
}
