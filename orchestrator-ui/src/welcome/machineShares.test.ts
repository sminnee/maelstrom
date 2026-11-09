import { describe, expect, it } from 'vitest';
import type { MetricsSample } from '../protocol/entities';
import { machineShares } from './machineShares';

const GIB = 1024 ** 3;

function sample(cpuPercent: number, gates: [number, number][]): MetricsSample {
  return {
    at: '2026-09-02T09:00:00.000Z',
    cpuPercent,
    cpuCount: 8,
    memoryUsedBytes: 10 * GIB,
    memoryTotalBytes: 32 * GIB,
    cpuTemperatureC: null,
    gates: gates.map(([cpu, rss], i) => ({
      jobId: String(i),
      worktree: null,
      path: `/p/${i}`,
      usage: { cpuPercent: cpu, rssBytes: rss, processes: 1 },
    })),
  };
}

describe('machineShares', () => {
  // The common split is covered by the Welcome dialog test; these are the edges it cannot reach.
  it('clamps "other" at 0 when the gates read more than the machine', () => {
    expect(machineShares(sample(10, [[160, 12 * GIB]]))).toEqual({
      cpu: { gates: 20, other: 0 },
      memory: { gates: 12 * GIB, other: 0 },
    });
  });

  it("caps the gates' part at the machine's whole", () => {
    // 600% + 400% of one core on 8 cores is 125% of the machine.
    expect(
      machineShares(
        sample(90, [
          [600, 20 * GIB],
          [400, 20 * GIB],
        ]),
      ),
    ).toEqual({
      cpu: { gates: 100, other: 0 },
      memory: { gates: 32 * GIB, other: 0 },
    });
  });
});
