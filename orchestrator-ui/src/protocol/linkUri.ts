/**
 * A `mael:` URI as the transcript holds it: the target of a **Link card**.
 *
 * The server writes these from the agent's marker. It turns a document's task
 * and name into the document id, and an unknown form into `mael:missing`. See
 * `lib/domain/src/mael_domain/link_cards.py` for the forms an agent writes.
 */
export type LinkUri =
  | { kind: 'task'; project: string; taskId: string }
  | { kind: 'session'; agentId: string }
  | { kind: 'doc'; documentId: string; version?: number }
  | { kind: 'comm'; comm: string }
  | { kind: 'worktree'; project: string; nato: string }
  | { kind: 'pr'; project: string; number: number }
  | { kind: 'missing' };

export const LINK_SCHEME = 'mael:';

/** Whether `href` is a link card's target rather than an ordinary link. */
export const isLinkUri = (href: string | undefined): href is string =>
  !!href?.startsWith(LINK_SCHEME);

const MISSING: LinkUri = { kind: 'missing' };

function decode(part: string): string | null {
  try {
    return decodeURIComponent(part);
  } catch {
    return null;
  }
}

/** `uri` as the target it names. Anything that is not a form is `missing`. */
export function parseLinkUri(uri: string): LinkUri {
  if (!isLinkUri(uri)) return MISSING;
  const [path = '', query] = uri.slice(LINK_SCHEME.length).split('?', 2);
  const [kind, ...raw] = path.split('/');
  const parts = raw.map(decode);
  if (!parts.length || parts.some((part) => !part)) return MISSING;
  const [a, b] = parts as string[];
  if (query !== undefined && kind !== 'doc') return MISSING;
  switch (kind) {
    case 'task':
      return parts.length === 2 ? { kind, project: a!, taskId: b! } : MISSING;
    case 'session':
      return parts.length === 1 ? { kind, agentId: a! } : MISSING;
    case 'comm':
      return parts.length === 1 ? { kind, comm: a! } : MISSING;
    case 'worktree':
      return parts.length === 2 ? { kind, project: a!, nato: b! } : MISSING;
    case 'pr':
      return parts.length === 2 && /^\d+$/.test(b!)
        ? { kind, project: a!, number: Number(b) }
        : MISSING;
    case 'doc': {
      if (parts.length !== 1) return MISSING;
      if (query === undefined) return { kind, documentId: a! };
      const version = /^v=(\d+)$/.exec(query)?.[1];
      return version ? { kind, documentId: a!, version: Number(version) } : MISSING;
    }
    default:
      return MISSING;
  }
}
