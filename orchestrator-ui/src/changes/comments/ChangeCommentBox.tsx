import { actionIcon } from '../../ui/actionIcons';
import { AppButton } from '../../ui/AppButton';
import { DialogFooter } from '../../ui/Dialog';
import { TextArea } from '../../ui/TextArea';
import styles from './comments.module.css';

type Props =
  | {
      /** `line 5` or `lines 4-5`, for the field's name. */
      label: string;
      body: string;
      onBody: (body: string) => void;
      onAdd: () => void;
      onCancel: () => void;
    }
  | {
      label: string;
      body: string;
      /** Another box holds text, which Edit would discard. */
      editDisabled: boolean;
      onEdit: () => void;
      onDelete: () => void;
    };

/**
 * A change comment below its lines. With `onAdd` it is the box the user writes
 * in; without, it is an added comment that waits for the post.
 */
export function ChangeCommentBox(props: Props) {
  if (!('onAdd' in props)) {
    return (
      <div className={styles.box} role="note" aria-label={`Comment on ${props.label}`}>
        <p className={styles.body}>{props.body}</p>
        <div className={styles.actions}>
          <AppButton
            variant="quiet"
            disabled={props.editDisabled}
            title={props.editDisabled ? 'Add or cancel the open comment first' : undefined}
            onClick={props.onEdit}
          >
            Edit
          </AppButton>
          <AppButton variant="quiet" onClick={props.onDelete}>
            Delete
          </AppButton>
        </div>
      </div>
    );
  }
  const { label, body, onBody, onAdd, onCancel } = props;
  return (
    <div className={styles.box}>
      <TextArea
        grow
        rows={2}
        autoFocus
        aria-label={`Comment on ${label}`}
        placeholder={`Comment on ${label}`}
        value={body}
        onChange={(e) => onBody(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') onCancel();
        }}
      />
      <DialogFooter
        className={styles.composing}
        aside={
          <AppButton variant="link" onClick={onCancel}>
            Cancel
          </AppButton>
        }
      >
        <AppButton
          icon={actionIcon('comment')}
          variant="primary"
          disabled={!body.trim()}
          onClick={onAdd}
        >
          Add comment
        </AppButton>
      </DialogFooter>
    </div>
  );
}
