import type { Agent } from '../protocol/entities';
import type { AgentId } from '../protocol/ids';
import { progressOf } from '../protocol/progress';
import { clockTime } from '../protocol/time';
import { isLive } from './graph';
import type { WorldView } from './world';

/** The numeric segments after the parent's id, so `X.10` sorts after `X.2`. */
function ordinals(agent: Agent): number[] {
  return agent.id
    .slice(agent.parent.length + 1)
    .split('.')
    .map(Number);
}

function byOrdinal(a: Agent, b: Agent): number {
  const x = ordinals(a);
  const y = ordinals(b);
  for (let i = 0; i < Math.max(x.length, y.length); i += 1) {
    const d = (x[i] ?? 0) - (y[i] ?? 0);
    if (d !== 0) return d;
  }
  return 0;
}

function childrenOf(world: WorldView, agentId: AgentId, live: boolean): Agent[] {
  return Object.values(world.agents)
    .filter((a) => a.parent === agentId && isLive(a) === live)
    .sort(byOrdinal);
}

/**
 * The running subagents of `agentId`, in id order. A nested one (`X.1.1`) is a
 * child of the same top-level agent and sits in the same list, after `X.1`.
 *
 * A finished subagent is left out — see `docs/dev/orchestrator-ui.md`. The
 * state is read as it stands, so a subagent that speaks after its notification
 * comes back to the list.
 */
export function subagentsOf(world: WorldView, agentId: AgentId): Agent[] {
  return childrenOf(world, agentId, true);
}

/** The finished subagents of `agentId`, in the same order. */
export function finishedSubagentsOf(world: WorldView, agentId: AgentId): Agent[] {
  return childrenOf(world, agentId, false);
}

/**
 * A session row's words: start time · `Live` or end state · session id. The
 * node card and the **Tab picker** read the same.
 */
export function sessionMeta(agent: Agent, now: number): string {
  return [
    clockTime(agent.startedAt, now),
    // A live agent's state needs its attention and gate. An ended one's does not.
    isLive(agent) ? 'Live' : progressOf(undefined, agent, [], null).words,
    agent.session.slice(0, 8),
  ]
    .filter(Boolean)
    .join(' · ');
}
