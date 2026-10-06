import { useWorld } from '../api/useWorld';
import { useShowing } from '../layout/useShowing';
import type { AgentStatusFilter } from '../selectors/filters';
import { AGENT_STATUS_LABELS } from '../selectors/filters';
import { agentCounts } from '../selectors/usage';
import { useAppStore } from '../store/store';
import { SplitChip } from '../ui/SplitChip';
import styles from './AgentsChip.module.css';

/** The statuses a click steps through, in order. */
const CYCLE = ['all', 'working-idle', 'working'] as const;
type CycleStatus = (typeof CYCLE)[number];

const nextStatus = (status: AgentStatusFilter): CycleStatus => {
  // An off-cycle status, such as `planned`, steps as `all` does.
  const at = Math.max(
    CYCLE.findIndex((s) => s === status),
    0,
  );
  return CYCLE[(at + 1) % CYCLE.length]!;
};

/**
 * `agents 3/5` in the top bar: how many are mid-turn, over how many are alive.
 *
 * One chip rather than two, because the two counts are nested — working is a
 * subset of open. Shown as a fraction, the gap between them *is* the idle
 * count, which is the number the operator acts on, and it needs no second chip
 * or arithmetic to read.
 *
 * Untoned: agents at work is the normal state, so amber here would be lit all
 * day and dull the amber that means the operator is needed.
 *
 * While the Desk shows, it is also the quick control for the Desk's agent
 * status filter. See DESIGN.md, "Agents chip".
 */
export function AgentsChip() {
  const { world } = useWorld();
  const agentStatus = useAppStore((s) => s.ui.filters.agentStatus) ?? 'all';
  const setFilters = useAppStore((s) => s.setFilters);
  const deskShowing = useShowing().includes('canvas');
  const { open, working } = agentCounts(world.agents);
  // An empty desk has nothing to say. `0/0` would be a reading about nothing.
  if (open === 0) return null;
  const counts = `${working} of ${open} agents working, ${open - working} idle`;
  if (!deskShowing) {
    return (
      <SplitChip label="agents" title={counts}>
        {working}/{open}
      </SplitChip>
    );
  }

  const next = nextStatus(agentStatus);
  const title =
    `${counts}. Agent status: ${AGENT_STATUS_LABELS[agentStatus]}. ` +
    `Click to show ${AGENT_STATUS_LABELS[next]}.`;
  return (
    <SplitChip label="agents" title={title} onClick={() => setFilters({ agentStatus: next })}>
      {agentStatus === 'working-idle' ? (
        <>
          <span className={styles.faint}>{working}/</span>
          {open}
        </>
      ) : agentStatus === 'working' ? (
        <>
          {working}
          <span className={styles.faint}>/{open}</span>
        </>
      ) : (
        `${working}/${open}`
      )}
    </SplitChip>
  );
}
