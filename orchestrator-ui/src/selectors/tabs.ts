import type { TaskRow } from '../api/types';
import type { Agent, Phase, Worktree } from '../protocol/entities';
import type { WorldView } from './world';
import type { AgentId, ProjectId, TaskId, WorktreeId } from '../protocol/ids';
import { phaseForCommand } from '../protocol/phase';
import { isLive } from './graph';
import type { PanelTab } from '../store/uiSlice';

/** Add `tab` unless a tab with its key is open already. Either way it is the one to focus. */
export function openOrFocusTab(tabs: PanelTab[], tab: PanelTab): PanelTab[] {
  return tabs.some((t) => t.key === tab.key) ? tabs : [...tabs, tab];
}

/** The tabs and which of them is in view, as the store holds them. */
export interface TabState {
  tabs: PanelTab[];
  activeTabKey: string | null;
  /** Tab keys, most recently activated first. */
  tabRecency: string[];
}

/** `key` moved to the front of the recency list. */
export const touchTab = (recency: string[], key: string): string[] => [
  key,
  ...recency.filter((k) => k !== key),
];

/**
 * The most recently active of `keys`, else the first of them. A tab can be
 * open with no recency, when something set the tabs directly.
 */
export function mostRecentTab(keys: string[], recency: string[]): string | null {
  return recency.find((k) => keys.includes(k)) ?? keys[0] ?? null;
}

/**
 * Remove the tabs. If the active one goes, the most recent tab left in its
 * group takes over, else the most recent tab left anywhere.
 *
 * The group first, because the strip shows one group: a neighbour from
 * another group would switch the sidebar under the reader.
 */
export function closeTabs(
  state: TabState,
  keys: string[],
  groupOf: (tab: PanelTab) => string,
): TabState {
  const closing = new Set(keys);
  const tabs = state.tabs.filter((t) => !closing.has(t.key));
  const tabRecency = state.tabRecency.filter((k) => !closing.has(k));
  const active = state.tabs.find((t) => t.key === state.activeTabKey);
  if (!active || !closing.has(active.key))
    return { tabs, activeTabKey: state.activeTabKey, tabRecency };
  const group = groupOf(active);
  const sameGroup = tabs.filter((t) => groupOf(t) === group).map((t) => t.key);
  const activeTabKey =
    mostRecentTab(sameGroup, tabRecency) ??
    mostRecentTab(
      tabs.map((t) => t.key),
      tabRecency,
    );
  return { tabs, activeTabKey, tabRecency };
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

export const changesTab = (worktreeId: WorktreeId): PanelTab => ({
  key: `changes:${worktreeId}`,
  kind: 'changes',
  worktreeId,
});

export interface TabAttribution {
  /**
   * What the tab is, in one string the operator can match against: the
   * task's bare notebook id, or — for an agent with no task — its own agent
   * id. The panel sidebar names the project, so the id need not.
   * The agent id is the failover task id, not a different kind of thing,
   * so a free agent's tab reads in the same slot and the same register.
   * A changes tab names its worktree instead.
   */
  id: TaskId | AgentId;
  /** Null when the entity has left the world: the chip then draws no phase. */
  phase: Phase | null;
  agentId: AgentId | null;
  /**
   * What the tab holds, where that is not already obvious: the document's
   * title, or `changes`. A session has none — its id alone says which session it is, and a
   * dated id is long enough that a word beside it wins no reader.
   */
  label: string;
  /** The work's own name — the task's title, or a worktree's branch. Empty for a free agent. */
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
        id: task?.notebookId || agent?.taskId || tab.agentId,
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
        id: task?.notebookId || doc?.taskId || doc?.agentId || tab.documentId,
        phase: phaseOf(task),
        agentId: doc?.agentId ?? null,
        // `||`, not `??`: a document's title is agent-authored, so an empty
        // one is possible, and a label-less document tab reads as a session.
        label: doc?.title || 'Document',
        title: task?.title ?? '',
      };
    }
    case 'changes': {
      // By id, not nato: every project has a delta. See docs/dev/orchestrator-ui.md.
      const worktree = world.worktrees[tab.worktreeId];
      return {
        id: tab.worktreeId,
        phase: null,
        agentId: null,
        label: 'changes',
        title: worktree?.branch ?? '',
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
    case 'changes':
      return undefined;
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

/**
 * The agent a document belongs to: its own, else the top-level agent that
 * runs its task. A document can outlive the agent that wrote it.
 *
 * A task relaunched elsewhere leaves its exited agent in the world, so a
 * live agent wins over an exited one: the document goes where the work is.
 */
function agentForDocument(world: WorldView, documentId: string): Agent | undefined {
  const doc = world.documents[documentId];
  if (!doc) return undefined;
  const own = world.agents[doc.agentId];
  if (own) return own;
  if (!doc.taskId) return undefined;
  return Object.values(world.agents)
    .filter((a) => !a.parent && a.taskId === doc.taskId)
    .sort((a, b) => Number(isLive(b)) - Number(isLive(a)) || a.id.localeCompare(b.id))[0];
}

function agentForTab(world: WorldView, tab: PanelTab): Agent | undefined {
  switch (tab.kind) {
    case 'session':
      return world.agents[tab.agentId];
    case 'document':
      return agentForDocument(world, tab.documentId);
    case 'changes':
      return undefined;
  }
}

/**
 * The worktree a tab belongs to: a changes tab's own, else the one its agent
 * runs in. `undefined` when the world cannot place it.
 */
export function worktreeForTab(world: WorldView, tab: PanelTab): Worktree | undefined {
  if (tab.kind === 'changes') return world.worktrees[tab.worktreeId];
  return world.worktrees[agentForTab(world, tab)?.worktreeId ?? ''];
}

/**
 * Where a tab sits in the sidebar: its worktree, its project, and the key of
 * its group — the worktree id, else `none:<project>`. The one place a tab is
 * placed, so the sidebar's groups and the close rule cannot disagree.
 */
function placeTab(
  world: WorldView,
  tab: PanelTab,
): { key: string; worktree: Worktree | null; project: ProjectId } {
  const worktree = worktreeForTab(world, tab) ?? null;
  const project =
    worktree?.project || agentForTab(world, tab)?.project || taskForTab(world, tab)?.project || '';
  return { key: worktree?.id ?? `none:${project}`, worktree, project };
}

/** The key of a tab's worktree group. */
export const groupKeyOf = (world: WorldView, tab: PanelTab): string => placeTab(world, tab).key;

/** The open tabs of one worktree: see CONTEXT.md, "Worktree group". */
export interface TabGroup {
  key: string;
  /** `null` for the tabs of a project that no worktree holds. */
  worktree: Worktree | null;
  label: string;
  tabs: PanelTab[];
}

/** One project's worktree groups. `project` is `''` for the tabs of no project. */
export interface ProjectGroup {
  project: ProjectId;
  label: string;
  worktrees: TabGroup[];
}

/** `_main` first, then by nato; the tabs of no worktree last. */
const worktreeRank = (g: TabGroup): [number, string] =>
  !g.worktree ? [2, ''] : g.worktree.nato === '_main' ? [0, ''] : [1, g.worktree.nato];

/**
 * The open tabs, grouped by project and then by worktree, for the sidebar.
 *
 * The order depends on names only, never on the order the tabs opened or the
 * world arrived in, so a row does not move when a tab opens.
 */
export function groupTabs(world: WorldView, tabs: PanelTab[]): ProjectGroup[] {
  const projects = new Map<ProjectId, Map<string, TabGroup>>();
  for (const tab of tabs) {
    const { key, worktree, project } = placeTab(world, tab);
    const groups = projects.get(project) ?? new Map<string, TabGroup>();
    projects.set(project, groups);
    const group = groups.get(key) ?? {
      key,
      worktree,
      label: worktree?.nato ?? 'no worktree',
      tabs: [],
    };
    groups.set(key, group);
    group.tabs.push(tab);
  }
  return [...projects]
    .map(([project, groups]) => ({
      project,
      label: project ? (world.projects[project]?.name ?? project) : 'Other',
      worktrees: [...groups.values()].sort((a, b) => {
        const [ra, na] = worktreeRank(a);
        const [rb, nb] = worktreeRank(b);
        return ra - rb || na.localeCompare(nb);
      }),
    }))
    .sort((a, b) => Number(!a.project) - Number(!b.project) || a.label.localeCompare(b.label));
}
