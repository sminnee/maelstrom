import type { DocumentStatus } from '../protocol/documents';
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

/** The current members of `doc`'s review group, in tag order. */
export function groupOf(world: WorldView, doc: DocumentRow): DocumentRow[] {
  return groupMembers(Object.values(world.documents), doc.group.id);
}
