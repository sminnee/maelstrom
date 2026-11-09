import { useState } from 'react';
import { bytes } from '../protocol/bytes';
import type { GateMetrics, MetricsSample } from '../protocol/entities';
import { gateCpuShare, machineShares } from './machineShares';
import { useMetricsStream } from '../live/useMetricsStream';
import styles from './MachineMetrics.module.css';

/** The span the charts cover: the history the server keeps. */
const WINDOW_MS = 15 * 60_000;
const WIDTH = 120;
const HEIGHT = 56;
/** The temperature chart's fixed scale, so a flat line reads as calm. */
const TEMP_MIN_C = 20;
const TEMP_MAX_C = 100;

/** The Welcome dialog's Machine entry: 15 minutes of charts, then each gate job's share. */
export function MachineMetrics() {
  const samples = useMetricsStream();
  const [hover, setHover] = useState<number | null>(null);
  if (samples === null) return <>…</>;
  const latest = samples.at(-1);
  if (!latest) return <span className={styles.faint}>No samples yet</span>;
  const shown = (hover === null ? undefined : samples[hover]) ?? latest;
  const end = Date.parse(latest.at);
  const chart = { samples, end, hover, onHover: setHover };
  const memTotal = Math.max(...samples.map((s) => s.memoryTotalBytes));
  const hasTemp = samples.some((s) => s.cpuTemperatureC !== null);
  return (
    <div className={styles.machine}>
      <div className={styles.charts}>
        <Chart
          label="CPU"
          current={shown.cpuPercent}
          format={percent}
          max={100}
          layers={[(s) => machineShares(s).cpu.gates, (s) => machineShares(s).cpu.other]}
          {...chart}
        />
        <Chart
          label="Memory"
          current={shown.memoryUsedBytes}
          format={bytes}
          max={memTotal}
          layers={[(s) => machineShares(s).memory.gates, (s) => machineShares(s).memory.other]}
          {...chart}
        />
        {hasTemp && (
          <Chart
            label="Temp"
            current={shown.cpuTemperatureC}
            format={celsius}
            min={TEMP_MIN_C}
            max={TEMP_MAX_C}
            line={(s) => s.cpuTemperatureC}
            {...chart}
          />
        )}
      </div>
      <div className={styles.legend} aria-hidden>
        <span className={styles.key} data-series="gates" /> gates
        <span className={styles.key} data-series="other" /> other
        {/* The hovered sample's time takes the window's place, so nothing moves. */}
        <span className={styles.faint}>
          · {hover === null ? 'last 15 min' : new Date(shown.at).toLocaleTimeString()}
        </span>
      </div>
      {shown.gates.length > 0 && <Figures sample={shown} />}
    </div>
  );
}

const percent = (v: number) => `${Math.round(v)}%`;
const celsius = (v: number) => `${Math.round(v)} °C`;

/** Each gate job's share of the sample, then "other". */
function Figures({ sample }: { sample: MetricsSample }) {
  const shares = machineShares(sample);
  return (
    <div className={styles.figures} data-testid="machine-now">
      {sample.gates.map((gate) => (
        <div key={gate.jobId} className={styles.part}>
          <span className={styles.mono}>{gateName(gate)}</span> cpu{' '}
          {Math.round(gateCpuShare(gate, sample.cpuCount))}% · mem {bytes(gate.usage.rssBytes)}
        </div>
      ))}
      <div className={styles.part}>
        other cpu {Math.round(shares.cpu.other)}% · mem {bytes(shares.memory.other)}
      </div>
    </div>
  );
}

function gateName(gate: GateMetrics): string {
  return gate.worktree ?? gate.path.split('/').filter(Boolean).at(-1) ?? gate.path;
}

interface ChartProps {
  label: string;
  /** The figure for the latest sample, or for the hovered one. */
  current: number | null;
  /** Words for a figure: the header's value and the axis's top and bottom. */
  format: (v: number) => string;
  min?: number;
  max: number;
  /** Stacked areas, bottom first. */
  layers?: ((s: MetricsSample) => number)[];
  line?: (s: MetricsSample) => number | null;
  samples: MetricsSample[];
  end: number;
  hover: number | null;
  onHover: (index: number | null) => void;
}

/** One small chart over the window. A pointer on it picks the nearest sample. */
function Chart({
  label,
  current,
  format,
  min = 0,
  max,
  layers,
  line,
  samples,
  end,
  hover,
  onHover,
}: ChartProps) {
  const xs = samples.map((s) => ((Date.parse(s.at) - (end - WINDOW_MS)) / WINDOW_MS) * WIDTH);
  const y = (v: number) =>
    HEIGHT - ((Math.min(Math.max(v, min), max) - min) / (max - min || 1)) * HEIGHT;
  const paths: string[] = [];
  if (layers) {
    // Per sample, the running total under each layer: [0, first, first + second, ...].
    const stacks = samples.map((s) => {
      let total = 0;
      return [0, ...layers.map((layer) => (total += layer(s)))];
    });
    layers.forEach((_, k) => {
      const at = (level: number) =>
        stacks.map((stack, i) => `${xs[i] ?? 0},${y(stack[level] ?? 0)}`);
      paths.push(`M${at(k + 1).join('L')}L${at(k).reverse().join('L')}Z`);
    });
  }
  const linePath = line
    ? samples.reduce((d, s, i) => {
        const v = line(s);
        return v === null ? d : `${d}${d ? 'L' : 'M'}${xs[i] ?? 0},${y(v)}`;
      }, '')
    : '';
  const pick = (event: React.PointerEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const x = ((event.clientX - box.left) / (box.width || 1)) * WIDTH;
    let nearest = 0;
    let gap = Infinity;
    xs.forEach((sx, i) => {
      if (Math.abs(sx - x) < gap) [nearest, gap] = [i, Math.abs(sx - x)];
    });
    onHover(nearest);
  };
  return (
    <div className={styles.chart} role="group" aria-label={label}>
      <div className={styles.head}>
        <span className={styles.label}>{label}</span>
        <span className={styles.value}>{current === null ? '–' : format(current)}</span>
      </div>
      <div className={styles.body}>
        <div className={styles.axis} aria-hidden>
          <span>{format(max)}</span>
          <span>{format(min)}</span>
        </div>
        <svg
          className={styles.plot}
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          preserveAspectRatio="none"
          role="img"
          aria-label={`${label}, last 15 minutes`}
          onPointerMove={pick}
          onPointerLeave={() => onHover(null)}
        >
          <line className={styles.baseline} x1={0} x2={WIDTH} y1={HEIGHT} y2={HEIGHT} />
          {/* The top of the scale, level with the axis's top label. */}
          <line className={styles.ceiling} x1={0} x2={WIDTH} y1={0} y2={0} />
          {paths.map((d, i) => (
            <path key={i} d={d} className={styles.area} data-series={i === 0 ? 'gates' : 'other'} />
          ))}
          {linePath && <path d={linePath} className={styles.line} />}
          {hover !== null && (
            <line
              className={styles.cursor}
              x1={xs[hover] ?? 0}
              x2={xs[hover] ?? 0}
              y1={0}
              y2={HEIGHT}
            />
          )}
        </svg>
      </div>
    </div>
  );
}
