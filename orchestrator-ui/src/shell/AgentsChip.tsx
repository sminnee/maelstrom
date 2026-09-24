import { useWorld } from '../api/useWorld';
import { agentCounts } from '../selectors/usage';
import { SplitChip } from '../ui/SplitChip';

/**
 * `agents 3/5` in the top bar: how many are mid-turn, over how many are alive.
 *
 * One chip rather than two, because the two counts are nested — working is a
 * subset of open. Shown as a fraction, the gap between them *is* the idle
 * count, which is the number the operator acts on, and it needs no second chip
 * or arithmetic to read.
 */
export function AgentsChip() {
  const { world } = useWorld();
  const { open, working } = agentCounts(world.agents);
  // An empty desk has nothing to say. `0/0` would be a reading about nothing.
  if (open === 0) return null;
  const idle = open - working;
  return (
    <SplitChip
      label="agents"
      tone={working > 0 ? 'busy' : 'neutral'}
      title={`${working} of ${open} agents working, ${idle} idle`}
    >
      {working}/{open}
    </SplitChip>
  );
}
