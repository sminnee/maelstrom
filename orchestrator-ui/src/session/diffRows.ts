import { diffLines } from 'diff';

export interface DiffRow {
  kind: 'context' | 'add' | 'remove';
  text: string;
}

/** The rows an Edit card draws, from its old and new strings. */
export function editToDiffRows(oldString: string, newString: string): DiffRow[] {
  const rows: DiffRow[] = [];
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
