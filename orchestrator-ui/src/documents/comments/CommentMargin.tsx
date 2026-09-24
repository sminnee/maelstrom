import { useState } from 'react';
import type { Anchor, Comment } from '../../protocol/documents';
import { describeError } from '../../api/http';
import { AppButton } from '../../ui/AppButton';
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
  /** Resolves once the comment was taken; a rejection keeps the draft for a retry. */
  onAdd: (anchor: Anchor, body: string) => void | Promise<unknown>;
  onCancel: () => void;
  onResolve: (commentId: string) => void | Promise<unknown>;
}) {
  const [body, setBody] = useState('');
  const add = async () => {
    if (!pending || !body.trim()) return;
    await onAdd(pending.anchor, body.trim());
    setBody('');
  };
  const composer = pending && (
    <div key="composer" className={styles.composer}>
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
        <AppButton
          variant="primary"
          errorChildren={describeError}
          disabled={!body.trim()}
          onClick={add}
        >
          Add comment
        </AppButton>
        <button type="button" className={styles.quiet} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
  const rows = comments.map((c) => (
    <div key={c.id} className={styles.comment} data-resolved={c.resolved || undefined}>
      <blockquote className={styles.quote}>{c.anchor.quote}</blockquote>
      <div className={styles.body}>{c.body}</div>
      <div className={styles.meta}>
        {c.author} · v{c.version}
        {c.resolved ? ' · resolved' : ''}
        {!c.resolved && (
          <AppButton
            className={styles.resolve}
            errorChildren={describeError}
            onClick={() => onResolve(c.id)}
          >
            Resolve
          </AppButton>
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
