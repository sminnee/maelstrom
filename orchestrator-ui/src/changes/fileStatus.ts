import type { FileDiff } from '../protocol/entities';

/** The one letter a file's status draws as, in the file list, the tree and the file head. */
export const STATUS_LETTER: Record<FileDiff['status'], string> = {
  added: 'A',
  modified: 'M',
  deleted: 'D',
  renamed: 'R',
};
