import type { Phase } from '../protocol/entities';
import type { World } from '../protocol/events';
import type { AgentId, TaskId } from '../protocol/ids';
import type { PanelTab } from '../store/uiSlice';
import { agentForTask } from './graph';

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

export const summaryTab = (taskId: TaskId): PanelTab => ({
  key: `summary:${taskId}`,
  kind: 'summary',
  taskId,
});
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
  taskId: TaskId;
  /** Null when the entity has left the world: the chip then draws no phase. */
  phase: Phase | null;
  agentId: AgentId | null;
  /** What the tab is: 'summary', 'session', or the document title. */
  title: string;
}

/** Which task (and phase) a tab belongs to, so two tabs from two agents are told apart. */
export function tabAttribution(world: World, tab: PanelTab): TabAttribution {
  switch (tab.kind) {
    case 'summary': {
      const task = world.tasks[tab.taskId];
      const agent = agentForTask(world, tab.taskId);
      return {
        taskId: tab.taskId,
        phase: task?.phase ?? agent?.phase ?? null,
        agentId: agent?.id ?? null,
        title: 'summary',
      };
    }
    case 'session': {
      const agent = world.agents[tab.agentId];
      return {
        taskId: agent?.taskId ?? '',
        phase: agent?.phase ?? null,
        agentId: tab.agentId,
        title: 'session',
      };
    }
    case 'document': {
      const doc = world.documents[tab.documentId];
      const task = doc ? world.tasks[doc.taskId] : undefined;
      return {
        taskId: doc?.taskId ?? '',
        phase: task?.phase ?? world.agents[doc?.agentId ?? '']?.phase ?? null,
        agentId: doc?.agentId ?? null,
        title: doc?.title ?? 'document',
      };
    }
  }
}

/** The task a tab points at, for `data-focused` on the canvas. */
export function focusedTaskId(
  world: World,
  tabs: PanelTab[],
  activeTabKey: string | null,
): TaskId | null {
  const tab = tabs.find((t) => t.key === activeTabKey);
  return tab ? tabAttribution(world, tab).taskId || null : null;
}
