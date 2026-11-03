import { useHost } from '../api/host';
import { useLayoutMode } from '../layout/useLayoutMode';
import type { HostUsage } from '../protocol/entities';
import type { UsageKey } from '../selectors/usage';
import { isNotable, usageChip } from '../selectors/usage';
import { SplitChip } from '../ui/SplitChip';
import { useNow } from '../ui/useNow';

/** One account's chips: whose budget, and what each window is called. */
interface Account {
  name: string;
  usage: HostUsage | null | undefined;
  labels: Record<UsageKey, string>;
  /** Windows that show only when notable, even on the wide bar. */
  onlyWhenNotable: UsageKey[];
}

/**
 * `5h 7%` and `week 24%` in the top bar: what the account has spent. Then
 * `week gpt 31%` for the Codex account, and `5h gpt` when it is ahead of pace.
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
  const narrow = useLayoutMode() === 'narrow';
  const host = data?.host ?? undefined;
  const accounts: Account[] = [
    {
      name: '',
      usage: host?.usage,
      labels: { fiveHour: '5h', sevenDay: 'week' },
      onlyWhenNotable: [],
    },
    {
      name: 'Codex',
      usage: host?.codexUsage,
      labels: { fiveHour: '5h gpt', sevenDay: 'week gpt' },
      // A second 5-hour chip crowds the bar; show it only ahead of pace.
      onlyWhenNotable: ['fiveHour'],
    },
  ];
  return (
    <>
      {accounts.flatMap((account) =>
        (['fiveHour', 'sevenDay'] as const).map((key) => {
          const chip = usageChip(account.usage, key, now, account.name);
          // No chip rather than `0%`: a bar that shows nothing is honest about
          // knowing nothing.
          if (!chip) return null;
          // A band is scarce on a phone, and an `onlyWhenNotable` window earns
          // none: show only a reading the operator would act on.
          if ((narrow || account.onlyWhenNotable.includes(key)) && !isNotable(chip)) return null;
          return (
            <SplitChip
              key={`${account.name}:${key}`}
              label={account.labels[key]}
              tone={chip.tone}
              stale={chip.stale}
              title={chip.title}
            >
              {chip.percent}
            </SplitChip>
          );
        }),
      )}
    </>
  );
}
