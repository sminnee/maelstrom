import { useHost } from '../api/host';
import type { HostUsage } from '../protocol/entities';
import type { UsageKey } from '../selectors/usage';
import { isNotable, usageChip, usageFor } from '../selectors/usage';
import { useAppStore } from '../store/store';
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
 * `5h 7%` and `week 24%` in the top bar, or in the Welcome dialog on the
 * narrow layout: what the account has spent. Then
 * `week gpt 31%` for the Codex account, and `5h gpt` when it is ahead of pace.
 *
 * The usage-shaped adapter over `SplitChip`, as `PrChip` is the PR-shaped one:
 * it decides what a reading means and the chip knows none of it.
 *
 * It reads the clock through `useNow` because pace moves on its own. No event
 * comes while the desk is quiet, but the budget by now still grows, and the
 * window still resets. Without a tick the tone would hold a morning pace all
 * afternoon.
 *
 * A click on a seven-day chip opens the Budget chart. See DESIGN.md, "Budget chart".
 */
export function UsageChips() {
  const { data } = useHost();
  const now = useNow();
  const openUsage = useAppStore((s) => s.openUsage);
  const host = data?.host ?? undefined;
  const accounts: Account[] = [
    {
      name: '',
      usage: usageFor(host, ''),
      labels: { fiveHour: '5h', sevenDay: 'week' },
      onlyWhenNotable: [],
    },
    {
      name: 'Codex',
      usage: usageFor(host, 'Codex'),
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
          // An `onlyWhenNotable` window earns no chip until the operator would
          // act on it.
          if (account.onlyWhenNotable.includes(key) && !isNotable(chip)) return null;
          return (
            <SplitChip
              key={`${account.name}:${key}`}
              label={account.labels[key]}
              tone={chip.tone}
              stale={chip.stale}
              title={chip.title}
              onClick={key === 'sevenDay' ? () => openUsage(account.name) : undefined}
            >
              {chip.percent}
            </SplitChip>
          );
        }),
      )}
    </>
  );
}
