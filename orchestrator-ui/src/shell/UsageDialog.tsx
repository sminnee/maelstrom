import { useHost } from '../api/host';
import type { HostUsage } from '../protocol/entities';
import type { BudgetSchedule } from '../selectors/budgetSchedule';
import { budgetCaption, budgetSchedule } from '../selectors/budgetSchedule';
import { asOf, usageFor, usageTone, windowName } from '../selectors/usage';
import type { ChipTone } from '../protocol/chipTone';
import { useAppStore } from '../store/store';
import { Dialog, DialogHeader } from '../ui/Dialog';
import { useNow } from '../ui/useNow';
import styles from './UsageDialog.module.css';

const WIDTH = 360;
const HEIGHT = 180;
/** Room for the axis labels, inside the view box. */
const PAD = { left: 34, right: 8, top: 8, bottom: 20 };
const PLOT_W = WIDTH - PAD.left - PAD.right;
const PLOT_H = HEIGHT - PAD.top - PAD.bottom;

/**
 * The **Budget chart** of one account's seven-day window: the budget schedule,
 * the spend now, and the two gaps between them. See DESIGN.md, "Budget chart".
 */
export function UsageDialog({
  usage,
  account,
  now,
  onClose,
}: {
  usage: HostUsage | null | undefined;
  /** Whose budget, such as `Codex`. Empty is the Claude account. */
  account: string;
  now: number;
  onClose: () => void;
}) {
  const name = windowName('sevenDay', account);
  const window = usage?.sevenDay;
  const schedule = window ? budgetSchedule('sevenDay', window, now) : null;
  const tone = usageTone(schedule?.quotient ?? null);
  const aged = usage ? asOf(usage.at, now) : '';
  const caption = schedule ? budgetCaption(schedule, clock) : 'No reading dates this window.';
  return (
    <Dialog label={name} onClose={onClose} className={styles.dialog}>
      <DialogHeader title={name} onClose={onClose} />
      {schedule && <Chart schedule={schedule} tone={tone} caption={caption} />}
      <p className={styles.caption}>
        {caption}
        {aged && <span className={styles.faint}> Reading {aged}.</span>}
      </p>
    </Dialog>
  );
}

const clockFormat = new Intl.DateTimeFormat(undefined, {
  weekday: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

/** `Thu 14:00`, in the browser's zone, as the app's other times are. */
function clock(ms: number): string {
  return clockFormat.format(new Date(ms)).replace(',', '');
}

const dayFormat = new Intl.DateTimeFormat(undefined, { weekday: 'short' });

/** Each local midnight inside `(start, end)`. */
function midnights(start: number, end: number): number[] {
  const out: number[] = [];
  const day = new Date(start);
  day.setHours(24, 0, 0, 0);
  while (day.getTime() < end) {
    out.push(day.getTime());
    day.setDate(day.getDate() + 1);
  }
  return out;
}

function Chart({
  schedule: s,
  tone,
  caption,
}: {
  schedule: BudgetSchedule;
  tone: ChipTone;
  caption: string;
}) {
  const x = (t: number) =>
    PAD.left + ((Math.min(Math.max(t, s.start), s.end) - s.start) / (s.end - s.start)) * PLOT_W;
  const y = (f: number) => PAD.top + (1 - Math.min(Math.max(f, 0), 1)) * PLOT_H;
  const path = s.curve.map(([t, f], i) => `${i ? 'L' : 'M'}${x(t)},${y(f)}`).join('');
  const nowX = x(s.now);
  const budgetY = y(s.budgetedNow);
  const spentY = y(s.spent);
  const atX = x(s.budgetedAt);
  const days = midnights(s.start, s.end);
  return (
    <svg
      className={styles.chart}
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      role="img"
      aria-label={caption}
    >
      {[0, 0.5, 1].map((f) => (
        <g key={f}>
          <line className={styles.grid} x1={PAD.left} x2={WIDTH - PAD.right} y1={y(f)} y2={y(f)} />
          <text className={styles.tick} x={PAD.left - 4} y={y(f)} textAnchor="end" dy="0.32em">
            {Math.round(f * 100)}%
          </text>
        </g>
      ))}
      {days.map((t) => (
        <g key={t}>
          <line className={styles.grid} x1={x(t)} x2={x(t)} y1={PAD.top} y2={HEIGHT - PAD.bottom} />
          <text className={styles.tick} x={x(t) + 2} y={HEIGHT - 6}>
            {dayFormat.format(new Date(t))}
          </text>
        </g>
      ))}
      <path className={styles.curve} d={path} />
      {/* Budget by now: up from the time now to the curve, across to the axis. */}
      <path className={styles.guide} d={`M${nowX},${y(0)}V${budgetY}H${PAD.left}`} />
      {/* The time the schedule budgeted the spend for: across to the curve, down to the axis. */}
      <path className={styles.guide} d={`M${nowX},${spentY}H${atX}V${y(0)}`} />
      <circle className={styles.spent} data-tone={tone} cx={nowX} cy={spentY} r={4.5} />
    </svg>
  );
}

/**
 * The one place the Budget chart draws. A seven-day usage chip opens it through
 * the store. Each shell mounts it, because the narrow bar shows its chips in
 * the Welcome dialog.
 */
export function UsageOverlay() {
  const { data } = useHost();
  const now = useNow();
  const { open, account } = useAppStore((s) => s.ui.usage);
  const closeUsage = useAppStore((s) => s.closeUsage);
  if (!open) return null;
  return (
    <UsageDialog
      usage={usageFor(data?.host, account)}
      account={account}
      now={now}
      onClose={closeUsage}
    />
  );
}
