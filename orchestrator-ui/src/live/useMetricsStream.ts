import { useContext, useEffect, useState } from 'react';
import type { MetricsSample } from '../protocol/entities';
import { SocketFactoryContext } from './socketFactory';
import type { SocketLike } from './socketLike';

/** The samples the server keeps: 15 minutes at one per 15 s. */
export const METRICS_HISTORY = 60;
const RECONNECT_MS = 1000;
const RECONNECT_MAX_MS = 30_000;

type MetricsFrame =
  | { type: 'metrics.snapshot'; samples: MetricsSample[] }
  | { type: 'metrics.sample'; sample: MetricsSample };

/**
 * The machine metrics history, oldest first, kept live while the caller is
 * mounted. `null` until the first frame. A dropped socket reconnects with a
 * backoff, and its snapshot replaces what was kept.
 */
export function useMetricsStream(opts: { reconnectMs?: number } = {}): MetricsSample[] | null {
  const factory = useContext(SocketFactoryContext);
  const firstDelay = opts.reconnectMs ?? RECONNECT_MS;
  const [samples, setSamples] = useState<MetricsSample[] | null>(null);
  useEffect(() => {
    let socket: SocketLike | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let delay = firstDelay;
    let stopped = false;
    const open = () => {
      const s = factory('/api/machine/metrics/stream');
      socket = s;
      s.onmessage = (event) => {
        delay = firstDelay;
        const frame = JSON.parse(event.data) as MetricsFrame;
        if (frame.type === 'metrics.snapshot') setSamples(frame.samples.slice(-METRICS_HISTORY));
        else if (frame.type === 'metrics.sample')
          setSamples((kept) => [...(kept ?? []), frame.sample].slice(-METRICS_HISTORY));
      };
      s.onclose = () => {
        if (stopped || socket !== s) return;
        timer = setTimeout(open, delay);
        delay = Math.min(delay * 2, RECONNECT_MAX_MS);
      };
    };
    open();
    return () => {
      stopped = true;
      clearTimeout(timer);
      socket?.close();
    };
  }, [factory, firstDelay]);
  return samples;
}
