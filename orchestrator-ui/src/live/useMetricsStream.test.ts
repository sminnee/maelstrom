import { act, renderHook } from '@testing-library/react';
import { createElement, type ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { FakeSocket } from '../fake/fakeSocket';
import type { MetricsSample } from '../protocol/entities';
import { SocketFactoryContext } from './socketFactory';
import { useMetricsStream } from './useMetricsStream';

function sample(n: number): MetricsSample {
  return {
    at: new Date(n * 15_000).toISOString(),
    cpuPercent: n,
    cpuCount: 1,
    memoryUsedBytes: 0,
    memoryTotalBytes: 1,
    cpuTemperatureC: null,
    gates: [],
  };
}

function render() {
  const sockets: FakeSocket[] = [];
  const factory = (path: string) => {
    const socket = new FakeSocket(path);
    sockets.push(socket);
    return socket;
  };
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(SocketFactoryContext.Provider, { value: factory }, children);
  const hook = renderHook(() => useMetricsStream({ reconnectMs: 1 }), { wrapper });
  return { hook, sockets };
}

const cpu = (samples: MetricsSample[] | null) => samples?.map((s) => s.cpuPercent);

describe('useMetricsStream', () => {
  it('holds nothing before the first frame, then the snapshot and each new sample', () => {
    const { hook, sockets } = render();
    expect(hook.result.current).toBeNull();
    expect(sockets[0]!.url).toBe('/api/machine/metrics/stream');

    act(() => sockets[0]!.receive({ type: 'metrics.snapshot', samples: [sample(1), sample(2)] }));
    act(() => sockets[0]!.receive({ type: 'metrics.sample', sample: sample(3) }));
    expect(cpu(hook.result.current)).toEqual([1, 2, 3]);
  });

  it('keeps the last 60 samples', () => {
    const { hook, sockets } = render();
    const full = Array.from({ length: 60 }, (_, i) => sample(i));
    act(() => sockets[0]!.receive({ type: 'metrics.snapshot', samples: full }));
    act(() => sockets[0]!.receive({ type: 'metrics.sample', sample: sample(60) }));
    expect(hook.result.current).toHaveLength(60);
    expect(cpu(hook.result.current)?.[0]).toBe(1);
  });

  it('reconnects after a drop, and the new snapshot replaces what was kept', async () => {
    const { hook, sockets } = render();
    act(() => sockets[0]!.receive({ type: 'metrics.snapshot', samples: [sample(1)] }));
    act(() => sockets[0]!.serverClose(4409));
    await vi.waitFor(() => expect(sockets).toHaveLength(2));
    act(() => sockets[1]!.receive({ type: 'metrics.snapshot', samples: [sample(7), sample(8)] }));
    expect(cpu(hook.result.current)).toEqual([7, 8]);
  });

  it('closes its socket on unmount', () => {
    const { hook, sockets } = render();
    hook.unmount();
    expect(sockets[0]!.closed).toBe(true);
  });
});
