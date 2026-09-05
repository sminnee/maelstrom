/** A document's status in words: `awaiting-review` becomes "awaiting review". */
export function describeDocumentStatus(status: string): string {
  return status.replace(/-/g, ' ');
}
