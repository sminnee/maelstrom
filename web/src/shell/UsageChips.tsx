import { useHost } from '../api/host';
import type { UsageKey } from '../selectors/usage';
import { usageChip } from '../selectors/usage';
import { SplitChip } from '../ui/SplitChip';
import { useNow } from '../ui/useNow';

const LABELS: Record<UsageKey, string> = { fiveHour: '5h', sevenDay: 'week' };

/**
 * `5h 7%` and `week 24%` in the top bar: what the account has spent.
 *
 * The usage-shaped adapter over `SplitChip`, as `PrChip` is the PR-shaped one:
 * it decides what a reading means and the chip knows none of it.
 *
 * It reads the clock through `useNow` because staleness arrives on its own.
 * No event says a reading has gone old — it goes old by nothing happening, so
 * without a tick the bar would hold a morning figure all afternoon.
 */
export function UsageChips() {
  const { data } = useHost();
  const now = useNow();
  const host = data?.host ?? undefined;
  return (
    <>
      {(['fiveHour', 'sevenDay'] as const).map((key) => {
        const chip = usageChip(host, key, now);
        // No chip rather than `0%`: a bar that shows nothing is honest about
        // knowing nothing.
        if (!chip) return null;
        return (
          <SplitChip
            key={key}
            label={LABELS[key]}
            tone={chip.tone}
            stale={chip.stale}
            title={chip.title}
          >
            {chip.percent}
          </SplitChip>
        );
      })}
    </>
  );
}
