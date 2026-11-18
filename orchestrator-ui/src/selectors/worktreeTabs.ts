import type { TaskRow } from '../api/types';
import type { Agent, Worktree } from '../protocol/entities';
import { splitDeskId } from '../protocol/deskId';
import { sessionMeta } from './agents';
import { cardDocuments, reviewGroups } from './documents';
import { agentsOfTask, worktreeHolders } from './graph';
import { describeDocumentStatus } from './status';
import { changesTab, documentTab, sessionTab, worktreeForTab, type TabOf } from './tabs';
import type { DocumentRow, WorldView } from './world';

/** One tab the **Tab picker** offers. */
export interface Offer {
  /** A dev env tab is not offered: each offer has a panel route. */
  tab: TabOf<'session' | 'document' | 'changes'>;
  label: string;
  /** The task id or free agent id, when the worktree has more than one of them. */
  prefix: string | null;
}

/** One document of a desk entry's card, with the title its card row reads. */
interface CardDocument {
  doc: DocumentRow;
  title: string;
  /** Another task owns it; this entry's task links it. */
  linked: boolean;
}

/** One desk entry's part of a worktree. */
export interface DeskWork {
  /** The id a row shows: the task's notebook id, or the free agent's own id. */
  owner: string;
  task: TaskRow | null;
  /** The card's sessions that open in the worktree. */
  sessions: Agent[];
  /** The card's documents that open in the worktree. Archived ones are left out. */
  documents: CardDocument[];
}

/** What the **Tab picker** of one worktree offers. */
export interface WorktreeTabOffers {
  /** `null` for a closed worktree: it has no changes to show. */
  changes: TabOf<'changes'> | null;
  /** Newest first. */
  sessions: Offer[];
  documents: Offer[];
}

/**
 * The desk work of worktree `worktree`. A desk entry belongs to it when its
 * node card has a session or a document that opens in this worktree, or when
 * it is a **Holder** of the worktree. `worktreeForTab` places each tab, the
 * rule the panel sidebar groups by, so the picker lists what the card would
 * open here. An entry off the desk has no card, and is left out.
 */
export function worktreeWork(world: WorldView, worktree: Worktree): DeskWork[] {
  const holders = worktreeHolders(world, worktree);
  const byTask = agentsOfTask(world);
  const here = (tab: TabOf<'session' | 'document'>) =>
    worktreeForTab(world, tab)?.id === worktree.id;
  const work: DeskWork[] = [];
  for (const deskId of Object.keys(world.desk)) {
    const named = splitDeskId(deskId);
    const task = named?.kind === 'task' ? world.tasks[named.id] : undefined;
    const agent = named?.kind === 'agent' ? world.agents[named.id] : undefined;
    if (!task && !agent) continue;
    const sessions = (task ? (byTask.get(task.id) ?? []) : [agent!]).filter((a) =>
      here(sessionTab(a.id)),
    );
    const { owned, linked } = cardDocuments(world, task?.id ?? null, agent?.id ?? null);
    const documents = [
      ...reviewGroups(owned).flatMap((g) =>
        g.members.map((doc) => ({ doc, title: doc.title, linked: false })),
      ),
      ...linked.map((g) => ({ doc: g.members[0]!, title: g.title, linked: true })),
    ].filter(({ doc }) => !doc.archived && here(documentTab(doc.id)));
    if (sessions.length + documents.length === 0 && !holders.has(deskId)) continue;
    work.push({
      owner: task ? task.notebookId || task.id : agent!.id,
      task: task ?? null,
      sessions,
      documents,
    });
  }
  return work.sort((a, b) => a.owner.localeCompare(b.owner, undefined, { numeric: true }));
}

const documentLabel = ({ doc, title }: CardDocument) =>
  `${title} v${doc.version} · ${describeDocumentStatus(doc.status)}`;

/** Each tab worktree `worktree` can show, for the **Tab picker**: see `worktreeWork`. */
export function worktreeTabOffers(
  world: WorldView,
  worktree: Worktree,
  now: number,
): WorktreeTabOffers {
  const work = worktreeWork(world, worktree);
  const prefix = (w: DeskWork) => (work.length > 1 ? w.owner : null);
  const sessions = work
    .flatMap((w) => w.sessions.map((agent) => ({ agent, prefix: prefix(w) })))
    .sort((a, b) => b.agent.startedAt.localeCompare(a.agent.startedAt))
    .map(({ agent, prefix }) => ({
      tab: sessionTab(agent.id),
      label: sessionMeta(agent, now),
      prefix,
    }));
  // Owned documents first, so a document a task here owns reads as its own
  // and not as another task's link. Each document shows once.
  const seen = new Set<string>();
  const documents = [false, true].flatMap((linked) =>
    work.flatMap((w) =>
      w.documents
        .filter((d) => d.linked === linked && !seen.has(d.doc.id))
        .map((d) => {
          seen.add(d.doc.id);
          return { tab: documentTab(d.doc.id), label: documentLabel(d), prefix: prefix(w) };
        }),
    ),
  );
  return {
    changes: worktree.isClosed ? null : changesTab(worktree.id),
    sessions,
    documents,
  };
}
