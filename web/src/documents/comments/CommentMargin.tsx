import { useState } from 'react';
import type { Anchor, Comment } from '../../protocol/documents';
import styles from './CommentMargin.module.css';

export function CommentMargin({
  comments,
  pending,
  onAdd,
  onCancel,
  onResolve,
}: {
  comments: Comment[];
  pending: Anchor | null;
  onAdd: (anchor: Anchor, body: string) => void;
  onCancel: () => void;
  onResolve: (commentId: string) => void;
}) {
  const [body, setBody] = useState('');
  return (
    <aside className={styles.margin} data-testid="comment-margin">
      {pending && (
        <form
          className={styles.composer}
          onSubmit={(e) => {
            e.preventDefault();
            if (!body.trim()) return;
            onAdd(pending, body.trim());
            setBody('');
          }}
        >
          <blockquote className={styles.quote}>{pending.quote}</blockquote>
          <textarea
            aria-label="Comment"
            rows={3}
            value={body}
            autoFocus
            onChange={(e) => setBody(e.target.value)}
            placeholder="What should change here?"
          />
          <div className={styles.row}>
            <button type="submit" disabled={!body.trim()}>
              Add comment
            </button>
            <button type="button" onClick={onCancel}>
              Cancel
            </button>
          </div>
        </form>
      )}
      {comments.length === 0 && !pending && (
        <div className={styles.hint}>Select text to comment on it.</div>
      )}
      {comments.map((c) => (
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
      ))}
    </aside>
  );
}
