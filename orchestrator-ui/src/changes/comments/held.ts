import type { ChangeComment } from '../../protocol/entities';
import type { HeldComments, Span } from './selection';

/**
 * Every change to the held set. There is one open box, and it keeps its text
 * until Add comment or Cancel: no other act discards what the user typed.
 */

/** An object, because `useRetained` merges a held object over its initial value. */
export const NO_COMMENTS: HeldComments = { comments: [], open: null };

/** An id for a comment the client made. It keys the list; the server does not read it. */
const newCommentId = () => `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;

/** The open box on `span`. A box already open moves there with its text. */
export function openOn(held: HeldComments, rev: string, path: string, span: Span): HeldComments {
  return { ...held, open: { ...held.open, rev, path, ...span, body: held.open?.body ?? '' } };
}

export function withBody(held: HeldComments, body: string): HeldComments {
  return held.open ? { ...held, open: { ...held.open, body } } : held;
}

export function cancelled(held: HeldComments): HeldComments {
  return { ...held, open: null };
}

/** Whether the open box holds text that another comment's Edit would discard. */
export function openHoldsText(held: HeldComments): boolean {
  return held.open !== null && held.open.body.trim() !== '';
}

/** Whether Edit would discard text: the open box holds some, and it is another comment's. */
function editWouldDiscard(held: HeldComments, id: string): boolean {
  return openHoldsText(held) && held.open?.id !== id;
}

export function editing(held: HeldComments, comment: ChangeComment): HeldComments {
  return editWouldDiscard(held, comment.id) ? held : { ...held, open: comment };
}

/** The open box as an added comment: a new one at the end, an edited one in its place. */
export function withOpenAdded(held: HeldComments): HeldComments {
  const { open } = held;
  if (!open || !open.body.trim()) return held;
  const body = open.body.trim();
  const { id } = open;
  const comments = id
    ? held.comments.map((c) => (c.id === id ? { ...open, id, body } : c))
    : [...held.comments, { ...open, id: newCommentId(), body }];
  return { comments, open: null };
}

/**
 * The set less the comments in `ids`: a deleted one, or the ones a post sent.
 * The open box stays, and so does a comment the user added while the post ran.
 */
export function without(held: HeldComments, ids: string[]): HeldComments {
  return {
    comments: held.comments.filter((c) => !ids.includes(c.id)),
    open: held.open?.id !== undefined && ids.includes(held.open.id) ? null : held.open,
  };
}
