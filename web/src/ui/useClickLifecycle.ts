import { useEffect, useRef, useState } from 'react';

export type ClickState =
  { kind: 'ready' } | { kind: 'processing' } | { kind: 'error'; message: string; error: unknown };

const READY: ClickState = { kind: 'ready' };

/**
 * The life of one click: `processing` while a returned promise is pending,
 * `error` for `errorResetMs` after it rejects, `ready` otherwise.
 *
 * `run` never rethrows: React reports a rejection from an async handler as
 * unhandled. `onError` hears it instead.
 */
export function useClickLifecycle({
  errorResetMs = 3000,
  onError,
}: {
  /** How long the error shows before the state is ready again. `0` holds it until the next run. */
  errorResetMs?: number;
  onError?: (err: unknown) => void;
} = {}) {
  const [state, setState] = useState<ClickState>(READY);
  const mounted = useRef(true);
  const reset = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (reset.current) clearTimeout(reset.current);
    };
  }, []);

  const clearReset = () => {
    if (reset.current) clearTimeout(reset.current);
    reset.current = null;
  };

  const fail = (err: unknown) => {
    onError?.(err);
    if (!mounted.current) return;
    setState({ kind: 'error', message: messageOf(err), error: err });
    if (errorResetMs > 0) {
      reset.current = setTimeout(() => {
        reset.current = null;
        if (mounted.current) setState(READY);
      }, errorResetMs);
    }
  };

  const run = async (handler: () => void | Promise<unknown>) => {
    clearReset();
    let result: void | Promise<unknown>;
    try {
      result = handler();
    } catch (err) {
      fail(err);
      return;
    }
    if (!isThenable(result)) {
      if (state.kind !== 'ready') setState(READY);
      return;
    }
    setState({ kind: 'processing' });
    try {
      await result;
      if (mounted.current) setState(READY);
    } catch (err) {
      fail(err);
    }
  };

  return { state, run };
}

function isThenable(value: unknown): value is Promise<unknown> {
  return !!value && typeof (value as Promise<unknown>).then === 'function';
}

function messageOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
