import type { Story } from '@ladle/react';
import type { HostUsage } from '../protocol/entities';
import { UsageDialog } from './UsageDialog';

export default { title: 'Shell / Budget chart' };

// A week from Thu 2026-09-10 8am to Thu 2026-09-17 8am Auckland.
const RESETS_AT = Date.parse('2026-09-16T20:00:00.000Z') / 1000;
const FRI_2PM = Date.parse('2026-09-11T02:00:00.000Z'); // 16 of 60 weighted hours gone
const SAT_3PM = Date.parse('2026-09-12T03:00:00.000Z'); // 23.5 of 60

const usage = (utilization: number, at: number): HostUsage => ({
  fiveHour: null,
  sevenDay: { utilization, resetsAt: RESETS_AT },
  at: new Date(at).toISOString(),
});

const Chart = ({ spent, now, readAt = now }: { spent: number; now: number; readAt?: number }) => (
  <UsageDialog usage={usage(spent, readAt)} account="" now={now} onClose={() => {}} />
);

/** What to look at: the dot over the curve, amber, and the instant guide right of now. */
export const Ahead: Story = () => <Chart spent={0.4} now={FRI_2PM} />;

/** The dot under the curve, and the instant guide left of now, on Thursday. */
export const Behind: Story = () => <Chart spent={0.15} now={FRI_2PM} />;

/** Read six hours ago. The figures are for now; the caption ends `Reading as of 6h ago`. */
export const Aged: Story = () => (
  <Chart spent={0.4} now={FRI_2PM} readAt={FRI_2PM - 6 * 3_600_000} />
);

/** Saturday: the curve rises at half the weekday slope. */
export const Weekend: Story = () => <Chart spent={0.45} now={SAT_3PM} />;
