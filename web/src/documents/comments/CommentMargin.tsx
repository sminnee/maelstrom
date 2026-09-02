import { useState } from 'react';
import type { Anchor, Comment } from '../../protocol/documents';
import type { Placed } from './useSelectionComment';
import styles from './CommentMargin.module.css';

/**
 * The comments beside a document. While text is selected, a control sits
 * level with the selection; clicking it opens the composer in the list, in
 * document order among the comments.
 */
export function CommentMargin({
  comments,
  selection,
  pending,
  onStart,
  onAdd,
  onCancel,
  onResolve,
}: {
  comments: Comment[];
  selection: Placed | null;
  pending: Placed | null;
  onStart: () => void;
  /** Resolves true when the comment was taken; false keeps the draft for a retry. */
  onAdd: (anchor: Anchor, body: string) => Promise<boolean>;
  onCancel: () => void;
  onResolve: (commentId: string) => void;
}) {
  const [body, setBody] = useState('');
  const composer = pending && (
    <form
      key="composer"
      className={styles.composer}
      onSubmit={(e) => {
        e.preventDefault();
        if (!body.trim()) return;
        void onAdd(pending.anchor, body.trim()).then((ok) => {
          if (ok) setBody('');
        });
      }}
    >
      <blockquote className={styles.quote}>{pending.anchor.quote}</blockquote>
      <textarea
        aria-label="Comment"
        rows={3}
        value={body}
        autoFocus
        onChange={(e) => setBody(e.target.value)}
        placeholder="What should change here?"
      />
      <div className={styles.row}>
        <button type="submit" className={styles.primary} disabled={!body.trim()}>
          Add comment
        </button>
        <button type="button" className={styles.quiet} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
  const rows = comments.map((c) => (
    <div key={c.id} className={styles.comment} data-resolved={c.resolved || undefined}>
      <blockquote className={styles.quote}>{c.anchor.quote}</blockquote>
      <div className={styles.body}>{c.body}</div>
      <div className={styles.meta}>
        {c.author} · v{c.version}
        {c.resolved ? ' · resolved' : ''}
        {!c.resolved && (
          <button type="button" className={styles.resolve} onClick={() => onResolve(c.id)}>
            Resolve
          </button>
        )}
      </div>
    </div>
  ));
  if (composer && pending) {
    const at = comments.findIndex((c) => c.anchor.start > pending.anchor.start);
    rows.splice(at === -1 ? rows.length : at, 0, composer);
  }
  return (
    <aside className={styles.margin} data-testid="comment-margin">
      {selection && !pending && (
        <button
          type="button"
          className={styles.offer}
          style={{ top: selection.top }}
          onClick={onStart}
        >
          Comment on selection
        </button>
      )}
      {comments.length === 0 && !pending && !selection && (
        <div className={styles.hint}>Select text to comment on it.</div>
      )}
      {rows}
    </aside>
  );
}
