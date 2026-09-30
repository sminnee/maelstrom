import { diffLines } from 'diff';
import type { DiffRowData } from '../ui/DiffRow';

/** The rows an Edit card draws, from its old and new strings. */
export function editToDiffRows(oldString: string, newString: string): DiffRowData[] {
  const rows: DiffRowData[] = [];
  for (const change of diffLines(oldString, newString)) {
    const kind = change.added ? 'add' : change.removed ? 'remove' : 'context';
    const lines = change.value.split('\n');
    // split leaves one empty string after a final newline; a real blank line
    // in the change is a separate element before it.
    if (change.value.endsWith('\n')) lines.pop();
    for (const text of lines) rows.push({ kind, text });
  }
  return rows;
}
