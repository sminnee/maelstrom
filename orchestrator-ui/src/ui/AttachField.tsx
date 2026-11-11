import { useId, useRef, useState, type ReactNode } from 'react';
import { useUploadAttachment, type Attachment } from '../api/attachments';
import { AppButton } from './AppButton';
import styles from './AttachField.module.css';

/**
 * The attach control every text surface shares: paste, pick, and a strip of
 * what is attached.
 *
 * It wraps the surface's own textarea rather than owning one, so each caller
 * keeps its own value, label and submit. What it owns is the interaction —
 * reading images off a paste, uploading files, and handing back the markdown
 * to append: an image ref and a thumbnail, or a link and a chip with its size.
 *
 * Paste takes images only, so pasted text still reaches the textarea.
 *
 * The upload happens on attach, not on send: a task edit that is never saved
 * leaves an orphan file rather than a half-written task, and every surface
 * shares one path to the server.
 */
export function AttachField({
  project,
  bucket,
  attached,
  onAttach,
  onRemove,
  disabled,
  className,
  actions,
  children,
}: {
  project: string;
  bucket: string;
  attached: Attachment[];
  /** Called once per uploaded file, with the markdown to append to the text. */
  onAttach: (attachment: Attachment) => void;
  /** The whole attachment, so the caller can strip its markdown from the text too. */
  onRemove: (attachment: Attachment) => void;
  disabled?: boolean;
  /** The caller's own layout for the wrapper: it sits in the caller's flow. */
  className?: string;
  /** The surface's own buttons, at the far end of the Attach row. */
  actions?: ReactNode;
  /** The surface's own textarea. */
  children: ReactNode;
}) {
  const upload = useUploadAttachment();
  const picker = useRef<HTMLInputElement>(null);
  const pickerId = useId();
  const [failed, setFailed] = useState<string[]>([]);

  /** Upload one file and attach it, or say why not. */
  const takeOne = async (file: File): Promise<string | null> => {
    try {
      onAttach(await upload.mutateAsync({ project, bucket, file }));
      return null;
    } catch (err) {
      // Name the file: with several selected, "that failed" does not say
      // which one is missing from the message about to be sent.
      return `${file.name}: ${err instanceof Error ? err.message : String(err)}`;
    }
  };

  const take = async (files: File[]) => {
    if (!files.length) return;
    setFailed([]);
    for (const file of files) {
      const why = await takeOne(file);
      if (why) setFailed((was) => [...was, why]);
    }
  };

  return (
    <div
      className={[styles.field, className].filter(Boolean).join(' ')}
      // A marker for the focus-mode label rule in `Dialog.module.css`: `:has`
      // cannot nest.
      data-field-box=""
      onPaste={(e) => {
        // A screenshot on the clipboard is a file item. Text pasted alongside
        // it must still reach the textarea, so only an image stops the event.
        if (disabled) return;
        const images = Array.from(e.clipboardData.files).filter((f) => f.type.startsWith('image/'));
        if (!images.length) return;
        e.preventDefault();
        void take(images);
      }}
    >
      {children}

      {attached.length > 0 && (
        <ul className={styles.strip}>
          {attached.map((item) => (
            <li key={item.url} className={styles.thumb}>
              {item.kind === 'image' ? (
                <img src={item.url} alt={item.name} />
              ) : (
                <span className={styles.doc} aria-hidden="true">
                  ▤
                </span>
              )}
              <span className={`${styles.name} truncate`} title={item.name}>
                {item.name}
              </span>
              {item.kind === 'file' && <span className={styles.size}>{humanSize(item.size)}</span>}
              <button
                type="button"
                aria-label={`Remove ${item.name}`}
                onClick={() => onRemove(item)}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className={styles.actions}>
        <input
          ref={picker}
          id={pickerId}
          type="file"
          multiple
          hidden
          aria-label="Attach file"
          onChange={(e) => {
            void take(Array.from(e.target.files ?? []));
            // Clear it, or picking the same file twice running fires no change.
            e.target.value = '';
          }}
        />
        <AppButton disabled={disabled || upload.isPending} onClick={() => picker.current?.click()}>
          {upload.isPending ? 'Attaching…' : 'Attach file'}
        </AppButton>
        {failed.length > 0 && (
          <span className={styles.error} role="alert">
            {failed.join('; ')}
          </span>
        )}
        {actions && <span className={styles.end}>{actions}</span>}
      </div>
    </div>
  );
}

/** A byte count in B, KB or MB, as the server's link label gives it. */
function humanSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}
