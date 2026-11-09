import type { GateMetrics, MetricsSample } from '../protocol/entities';

export interface MachineShares {
  /** Share of the whole machine, 0-100. */
  cpu: { gates: number; other: number };
  memory: { gates: number; other: number };
}

/** A gate job's CPU as a share of the whole machine: its figure is per core. */
export function gateCpuShare(gate: GateMetrics, cpuCount: number): number {
  return gate.usage.cpuPercent / Math.max(cpuCount, 1);
}

/**
 * The machine's load split into the gate jobs and the rest. The two readings
 * are not taken at one moment, so "other" is clamped at 0 and the gate part at
 * the machine's whole.
 */
export function machineShares(sample: MetricsSample): MachineShares {
  const gateCpu = Math.min(
    sample.gates.reduce((sum, g) => sum + gateCpuShare(g, sample.cpuCount), 0),
    100,
  );
  const gateMem = Math.min(
    sample.gates.reduce((sum, g) => sum + g.usage.rssBytes, 0),
    sample.memoryTotalBytes,
  );
  return {
    cpu: { gates: gateCpu, other: Math.max(sample.cpuPercent - gateCpu, 0) },
    memory: { gates: gateMem, other: Math.max(sample.memoryUsedBytes - gateMem, 0) },
  };
}
