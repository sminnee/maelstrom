export type DiffRowKind = 'context' | 'add' | 'remove';

/** The sign git writes before a row, which a quoted row keeps. */
export const SIGN: Record<DiffRowKind, string> = { add: '+', remove: '-', context: ' ' };
