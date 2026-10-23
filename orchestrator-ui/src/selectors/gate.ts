import type { Agent, WorktreeGate } from '../protocol/entities';
import type { WorldView } from './world';

/** The gate job of the agent's worktree, for `progressOf`, or `null` with none. */
export function gateOf(
  world: Pick<WorldView, 'worktrees'>,
  agent: Agent | undefined,
): WorktreeGate | null {
  return (agent && world.worktrees[agent.worktreeId]?.gate) ?? null;
}
