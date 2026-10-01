import type { DocumentKind, DocumentStatus } from '../protocol/documents';
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
 * member was dropped from its group, so it is left out — see `CONTEXT.md`,
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

/** The review groups of one heading on the node card. */
export interface KindSection {
  heading: string;
  groups: ReviewGroup[];
}

/** The card's headings, in the order it draws them. Every kind not named here is `Other`. */
const KIND_HEADINGS: [DocumentKind, string][] = [
  ['plan', 'Plans'],
  ['verification', 'Verifications'],
];
const OTHER_HEADING = 'Other';

/**
 * `documents` as review groups under a heading per kind: Plans, Verifications,
 * then Other. A group takes its head member's kind. A heading with no group is
 * left out.
 */
export function documentsByKind(documents: readonly DocumentRow[]): KindSection[] {
  const groups = reviewGroups(documents);
  const headingOf = (group: ReviewGroup) =>
    KIND_HEADINGS.find(([kind]) => kind === group.members[0]!.kind)?.[1] ?? OTHER_HEADING;
  return [...KIND_HEADINGS.map(([, heading]) => heading), OTHER_HEADING]
    .map((heading) => ({ heading, groups: groups.filter((g) => headingOf(g) === heading) }))
    .filter((section) => section.groups.length > 0);
}

/** The current members of `doc`'s review group, in tag order. */
export function groupOf(world: WorldView, doc: DocumentRow): DocumentRow[] {
  return groupMembers(Object.values(world.documents), doc.group.id);
}
