import type { TaskRow } from '../api/types';
import type { Phase } from '../protocol/entities';
import type { WorldView } from './world';
import type { AgentId, TaskId } from '../protocol/ids';
import { phaseForCommand } from '../protocol/phase';
import type { PanelTab } from '../store/uiSlice';

/** Add `tab` unless a tab with its key is open already. Either way it is the one to focus. */
export function openOrFocusTab(tabs: PanelTab[], tab: PanelTab): PanelTab[] {
  return tabs.some((t) => t.key === tab.key) ? tabs : [...tabs, tab];
}

/** Remove the tab; if it was active, its right neighbour (else left) takes over. */
export function closeTab(
  tabs: PanelTab[],
  activeTabKey: string | null,
  key: string,
): { tabs: PanelTab[]; activeTabKey: string | null } {
  const index = tabs.findIndex((t) => t.key === key);
  if (index === -1) return { tabs, activeTabKey };
  const remaining = tabs.filter((t) => t.key !== key);
  if (activeTabKey !== key) return { tabs: remaining, activeTabKey };
  const neighbour = remaining[index] ?? remaining[index - 1] ?? null;
  return { tabs: remaining, activeTabKey: neighbour?.key ?? null };
}

export const sessionTab = (agentId: AgentId): PanelTab => ({
  key: `session:${agentId}`,
  kind: 'session',
  agentId,
});
export const documentTab = (documentId: string): PanelTab => ({
  key: `document:${documentId}`,
  kind: 'document',
  documentId,
});

export interface TabAttribution {
  /**
   * What the tab is, in one string the operator can match against: the
   * qualified task id, or — for an agent with no task — its own agent id.
   * The agent id is the failover task id, not a different kind of thing,
   * so a free agent's tab reads in the same slot and the same register.
   */
  id: TaskId | AgentId;
  /** Null when the entity has left the world: the chip then draws no phase. */
  phase: Phase | null;
  agentId: AgentId | null;
  /**
   * What the tab holds, where that is not already obvious: the document's
   * title. A session has none — its id alone says which session it is, and a
   * qualified id is long enough that a word beside it wins no reader.
   */
  label: string;
  /** The work's own name — the task's title. Empty for a free agent. */
  title: string;
}

/** A tab can outlive its task, and a phase it cannot read is drawn as none. */
const phaseOf = (task: TaskRow | undefined): Phase | null =>
  task ? phaseForCommand(task.command) : null;

/** Which task (and phase) a tab belongs to, so two tabs from two agents are told apart. */
export function tabAttribution(world: WorldView, tab: PanelTab): TabAttribution {
  switch (tab.kind) {
    case 'session': {
      const agent = world.agents[tab.agentId];
      const task = taskForTab(world, tab);
      return {
        id: agent?.taskId || tab.agentId,
        phase: phaseOf(task),
        agentId: tab.agentId,
        label: '',
        title: task?.title ?? '',
      };
    }
    case 'document': {
      const doc = world.documents[tab.documentId];
      const task = taskForTab(world, tab);
      return {
        // `tab.documentId` is the last resort, as `tab.agentId` is for a
        // session: a tab the world can tell nothing about still names itself.
        id: task?.id || doc?.taskId || doc?.agentId || tab.documentId,
        phase: phaseOf(task),
        agentId: doc?.agentId ?? null,
        // `||`, not `??`: a document's title is agent-authored, so an empty
        // one is possible, and a label-less document tab reads as a session.
        label: doc?.title || 'Document',
        title: task?.title ?? '',
      };
    }
  }
}

/**
 * The task a tab belongs to, resolved once for every reader.
 *
 * A document can outlive its task. Its task then falls back to its agent's,
 * so a tab never names one task and colours another. `tabAttribution` and
 * `focusedTaskId` share this rather than each holding the chain: two copies
 * would let the canvas focus a different task than the tab names.
 */
function taskForTab(world: WorldView, tab: PanelTab): TaskRow | undefined {
  switch (tab.kind) {
    case 'session':
      return world.tasks[world.agents[tab.agentId]?.taskId ?? ''];
    case 'document': {
      const doc = world.documents[tab.documentId];
      const agent = world.agents[doc?.agentId ?? ''];
      return world.tasks[doc?.taskId ?? ''] ?? world.tasks[agent?.taskId ?? ''];
    }
  }
}

/**
 * The task a tab points at, for `data-focused` on the canvas.
 *
 * It reads the task explicitly rather than the attribution's `id`, which
 * fails over to an agent id: a free agent's tab focuses no node.
 */
export function focusedTaskId(
  world: WorldView,
  tabs: PanelTab[],
  activeTabKey: string | null,
): TaskId | null {
  const tab = tabs.find((t) => t.key === activeTabKey);
  return tab ? (taskForTab(world, tab)?.id ?? null) : null;
}
