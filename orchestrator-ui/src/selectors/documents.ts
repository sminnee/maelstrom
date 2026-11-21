import type { DocumentKind, DocumentLink, DocumentStatus } from '../protocol/documents';
import type { TaskId } from '../protocol/ids';
import type { DocumentRow, WorldView } from './world';

/** One review group as a list shows it. */
export interface ReviewGroup {
  id: string;
  title: string;
  status: DocumentStatus;
  /** The current members, in tag order. */
  members: DocumentRow[];
}

/**
 * The current members of review group `groupId`, in tag order. A superseded
 * member was dropped from its group, so it is left out — see `docs/specs/tasks/CONTEXT.md`,
 * "Review group".
 */
export function groupMembers<T extends DocumentRow>(documents: readonly T[], groupId: string): T[] {
  return documents
    .filter((d) => d.group.id === groupId && d.status !== 'superseded')
    .sort((a, b) => a.group.position - b.group.position);
}

/** `documents` as review groups, in the order each group first appears. */
export function reviewGroups(documents: readonly DocumentRow[]): ReviewGroup[] {
  const ids = [...new Set(documents.map((d) => d.group.id))];
  return ids.flatMap((id) => {
    const members = groupMembers(documents, id);
    const head = members[0];
    if (!head) return [];
    return [{ id, title: head.group.title, status: head.status, members }];
  });
}

/** One entry on the node card: a review group the task owns, or a document it links. */
export interface CardGroup extends ReviewGroup {
  /** The task links the document; another task owns it. */
  linked: boolean;
  /** The heading a linked entry names, which its link scrolls to. */
  fragment?: string;
}

/** The entries of one heading on the node card. */
export interface KindSection {
  heading: string;
  groups: CardGroup[];
  /** Archived entries, which the card shows collapsed at the end of the section. */
  archived: CardGroup[];
}

/** The card's headings, in the order it draws them. Every kind not named here is `Other`. */
const KIND_HEADINGS: [DocumentKind, string][] = [
  ['plan', 'Plans'],
  ['verification', 'Verifications'],
];
const OTHER_HEADING = 'Other';

/** The documents that link task `taskId`, one entry per link. */
export function linkedEntries(documents: readonly DocumentRow[], taskId: string): CardGroup[] {
  return documents.flatMap((d) =>
    d.links
      .filter((link) => link.taskId === taskId)
      .map((link) => ({
        id: `${d.id}#${link.fragment}`,
        title: link.fragment ? `${d.title} #${link.fragment}` : d.title,
        status: d.status,
        members: [d],
        linked: true,
        fragment: link.fragment,
      })),
  );
}

/**
 * The card's entries under a heading per kind: Plans, Verifications, then
 * Other. `owned` become review groups, and a group takes its head member's
 * kind. `linked` follow them. An archived entry goes to its section's
 * `archived`. A heading with no entry is left out.
 */
export function documentsByKind(
  owned: readonly DocumentRow[],
  linked: readonly CardGroup[] = [],
): KindSection[] {
  const entries: CardGroup[] = [
    ...reviewGroups(owned).map((g) => ({ ...g, linked: false })),
    ...linked,
  ];
  const headingOf = (group: CardGroup) =>
    KIND_HEADINGS.find(([kind]) => kind === group.members[0]!.kind)?.[1] ?? OTHER_HEADING;
  return [...KIND_HEADINGS.map(([, heading]) => heading), OTHER_HEADING]
    .map((heading) => {
      const here = entries.filter((g) => headingOf(g) === heading);
      return {
        heading,
        groups: here.filter((g) => !g.members[0]!.archived),
        archived: here.filter((g) => g.members[0]!.archived),
      };
    })
    .filter((section) => section.groups.length + section.archived.length > 0);
}

/**
 * The documents a node card lists: those its task owns, then those its task
 * links. A free agent has no task, so its card lists the documents of that
 * agent alone. The card and the **Tab picker** both read this.
 */
export function cardDocuments(
  world: WorldView,
  taskId: string | null,
  agentId: string | null,
): { owned: DocumentRow[]; linked: CardGroup[] } {
  const all = Object.values(world.documents);
  return {
    owned: all.filter((d) => (taskId ? d.taskId === taskId : !!agentId && d.agentId === agentId)),
    linked: taskId ? linkedEntries(all, taskId) : [],
  };
}

/** The current members of `doc`'s review group, in tag order. */
export function groupOf(world: WorldView, doc: DocumentRow): DocumentRow[] {
  return groupMembers(Object.values(world.documents), doc.group.id);
}

/** The tasks that link one place in a document: `fragment` is `''` for the whole. */
export interface FragmentLinks {
  fragment: string;
  taskIds: TaskId[];
}

/**
 * `links` grouped by the place each names: the whole document first, then
 * each fragment in the order of `fragments`, the document's heading order. A
 * fragment a later version dropped still has its links, so it follows them.
 */
export function linksByFragment(
  links: readonly DocumentLink[],
  fragments: readonly string[],
): FragmentLinks[] {
  const order = ['', ...fragments, ...links.map((l) => l.fragment)];
  return [...new Set(order)].flatMap((fragment) => {
    const taskIds = links.filter((l) => l.fragment === fragment).map((l) => l.taskId);
    return taskIds.length ? [{ fragment, taskIds }] : [];
  });
}
