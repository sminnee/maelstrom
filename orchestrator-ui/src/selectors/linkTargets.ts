import type { TaskRow } from '../api/types';
import { parseLinkUri, type LinkUri } from '../protocol/linkUri';
import type { Card } from '../nav/location';
import { isOpen, type Attention } from '../protocol/attention';
import type { Agent, Comm, Phase, Worktree, WorktreeGate } from '../protocol/entities';
import { phaseForTask } from '../protocol/phase';
import { progressOf, type NodeState } from '../protocol/progress';
import { worktreePr, type PrReading } from './cardPr';
import { commLabel } from './comms';
import { agentsByTask } from './graph';
import { describeDocumentStatus, describePrState } from './status';
import { documentTab, sessionTab, type TabOf } from './tabs';
import type { WorldView } from './world';
import { branchLabel, worktreeName } from './worktrees';

/** What a tap on a card opens. The card turns each into the nav move that opens it. */
export type LinkAction =
  | { kind: 'card'; card: Card }
  | { kind: 'tab'; tab: TabOf<'session'> | TabOf<'document'> }
  | { kind: 'comm'; commId: string }
  | { kind: 'worktree'; worktreeId: string }
  | { kind: 'external'; url: string };

/** The primary action's words. Only an awaiting document reads `Review`. */
export type LinkActionLabel = 'Open' | 'Review' | 'Open on GitHub';

/** A **Link card**'s target as the live world holds it now. */
export interface FoundLink {
  kind: Exclude<LinkUri['kind'], 'missing'>;
  title: string;
  /** The line under the title: an id or a branch, or `''`. */
  detail: string;
  /** The state in words, or `''`. */
  words: string;
  /** The `data-state` the card draws, in the deck row's vocabulary, or `''` for no dot. */
  state: NodeState | '';
  /** The `data-phase` of a task's card, so it takes the task's phase colour. */
  phase: Phase | null;
  pr?: PrReading;
  comm?: Comm;
  gate?: WorktreeGate;
  actionLabel: LinkActionLabel;
  action: LinkAction;
}

export type LinkResolution = FoundLink | { kind: 'missing' };

const MISSING: LinkResolution = { kind: 'missing' };

/** The open attention items on a task or an agent, oldest first. */
function openAttention(
  world: WorldView,
  taskId: string | null,
  agentId: string | undefined,
): Attention[] {
  return Object.values(world.attention)
    .filter(
      (item) =>
        isOpen(item) &&
        ((!!taskId && item.taskId === taskId) || (!!agentId && item.agentId === agentId)),
    )
    .sort((a, b) => a.raisedAt.localeCompare(b.raisedAt));
}

function gateOf(world: WorldView, agent: Agent | undefined): WorktreeGate | null {
  return (agent && world.worktrees[agent.worktreeId]?.gate) ?? null;
}

const found = (
  link: Omit<FoundLink, 'state' | 'phase' | 'words' | 'detail' | 'actionLabel'> &
    Partial<Pick<FoundLink, 'state' | 'phase' | 'words' | 'detail' | 'actionLabel'>>,
): FoundLink => ({
  detail: '',
  words: '',
  state: '',
  phase: null,
  actionLabel: 'Open',
  ...link,
});

function taskLink(world: WorldView, task: TaskRow): FoundLink {
  const agent = agentsByTask(world).get(task.id);
  const attention = openAttention(world, task.id, agent?.id);
  const progress = progressOf(task, agent, attention, gateOf(world, agent));
  return found({
    kind: 'task',
    title: task.title,
    detail: task.notebookId,
    words: attention[0]?.summary || progress.words,
    state: progress.state,
    phase: phaseForTask(task, agent),
    action: { kind: 'card', card: { kind: 'task', id: task.id } },
  });
}

function sessionLink(world: WorldView, agent: Agent): FoundLink {
  const task = agent.taskId ? world.tasks[agent.taskId] : undefined;
  const attention = openAttention(world, null, agent.id);
  const progress = progressOf(undefined, agent, attention, gateOf(world, agent));
  const waits = agent.pendingRequestIds.length > 0;
  return found({
    kind: 'session',
    title: agent.description || task?.title || 'Agent',
    detail: agent.id,
    words: waits ? 'waits on you' : progress.words,
    state: waits ? 'needs-attention' : progress.state,
    action: { kind: 'tab', tab: sessionTab(agent.id) },
  });
}

function documentLink(world: WorldView, documentId: string, version?: number): LinkResolution {
  const doc = world.documents[documentId];
  // A version past the latest is not one the document tab can open.
  if (!doc || (version !== undefined && (version < 1 || version > doc.version))) return MISSING;
  const shown = version ?? doc.version;
  const latest = shown === doc.version;
  const awaiting = latest && doc.status === 'awaiting-review';
  return found({
    kind: 'doc',
    title: `${doc.title} v${shown}`,
    // An earlier version's own verdict is not in the world: say what is.
    words: latest ? describeDocumentStatus(doc.status) : `v${doc.version} is the latest`,
    state: awaiting ? 'needs-attention' : '',
    actionLabel: awaiting ? 'Review' : 'Open',
    action: {
      kind: 'tab',
      tab: documentTab(documentId, version === undefined ? {} : { version }),
    },
  });
}

function commLink(comms: ReadonlyMap<string, Comm>, key: string): LinkResolution {
  const comm = comms.get(key) ?? [...comms.values()].find((c) => c.codename === key);
  if (!comm) return MISSING;
  return found({
    kind: 'comm',
    // The chip carries the codename, so the title is the comm's own.
    title: comm.title || commLabel(comm),
    words: comm.closedAt ? 'closed' : 'open',
    state: comm.closedAt ? 'done' : '',
    comm,
    action: { kind: 'comm', commId: comm.id },
  });
}

function worktreeLink(worktree: Worktree, ownPrefix: string): FoundLink {
  const pr = worktreePr(worktree);
  return found({
    kind: 'worktree',
    title: worktreeName(worktree, ownPrefix),
    detail: branchLabel(worktree),
    words: pr ? '' : 'no PR',
    pr,
    ...(worktree.gate ? { gate: worktree.gate } : {}),
    action: { kind: 'worktree', worktreeId: worktree.id },
  });
}

/**
 * The PR `number` of `project`: the worktree's reading when a worktree holds
 * it, else a task's **Registered PR**, which has no state.
 */
function prLink(world: WorldView, project: string, number: number): LinkResolution {
  const worktree = Object.values(world.worktrees).find(
    (w) => w.project === project && w.prNumber === number,
  );
  const fromWorktree = worktreePr(worktree);
  const task = fromWorktree
    ? undefined
    : Object.values(world.tasks).find(
        (t) => t.project === project && t.prs.some((p) => p.number === number),
      );
  const registered = task?.prs.find((p) => p.number === number);
  const pr: PrReading | undefined =
    fromWorktree ?? (registered && { ...registered, state: '', draft: false });
  if (!pr?.url) return MISSING;
  return found({
    kind: 'pr',
    // The chip carries the number, so the title is what the PR is for.
    title: worktree?.branch || task?.title || `#${number}`,
    words: describePrState(pr.state, pr.draft),
    pr,
    actionLabel: 'Open on GitHub',
    action: { kind: 'external', url: pr.url },
  });
}

/**
 * The card a `mael:` URI draws: its title, its state now, and its one action.
 *
 * Read from the live world, so a card in an old message shows the item as it
 * is now. A target the world does not hold, a comm `comms` does not hold, and
 * a `mael:missing` URI are all `missing`.
 */
export function resolveLinkTarget(
  world: WorldView,
  comms: ReadonlyMap<string, Comm>,
  uri: string,
  ownPrefix = '',
): LinkResolution {
  const target = parseLinkUri(uri);
  switch (target.kind) {
    case 'task': {
      const task = world.tasks[`${target.project}/${target.taskId}`];
      return task ? taskLink(world, task) : MISSING;
    }
    case 'session': {
      const agent = world.agents[target.agentId];
      return agent ? sessionLink(world, agent) : MISSING;
    }
    case 'doc':
      return documentLink(world, target.documentId, target.version);
    case 'comm':
      return commLink(comms, target.comm);
    case 'worktree': {
      const worktree = world.worktrees[`${target.project}-${target.nato}`];
      return worktree ? worktreeLink(worktree, ownPrefix) : MISSING;
    }
    case 'pr':
      return prLink(world, target.project, target.number);
    case 'missing':
      return MISSING;
  }
}
