import { useId, useRef, useState, type ReactNode } from 'react';
import { useUploadAttachment, type Attachment } from '../api/attachments';
import styles from './AttachField.module.css';

/**
 * The attach control every text surface shares: paste, pick, and a strip of
 * what is attached.
 *
 * It wraps the surface's own textarea rather than owning one, so each caller
 * keeps its own value, label and submit. What it owns is the interaction —
 * reading images off a paste, uploading them, and handing back the markdown
 * ref to append.
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
  children,
}: {
  project: string;
  bucket: string;
  attached: Attachment[];
  /** Called once per uploaded image, with the ref to append to the text. */
  onAttach: (attachment: Attachment) => void;
  /** The whole image, so the caller can strip its ref from the text too. */
  onRemove: (image: Attachment) => void;
  disabled?: boolean;
  /** The caller's own layout for the wrapper: it sits in the caller's flow. */
  className?: string;
  /** The surface's own textarea. */
  children: ReactNode;
}) {
  const upload = useUploadAttachment();
  const picker = useRef<HTMLInputElement>(null);
  const pickerId = useId();
  const [failed, setFailed] = useState<string[]>([]);

  const take = async (files: File[]) => {
    const images = files.filter((f) => f.type.startsWith('image/'));
    if (!images.length) return;
    setFailed([]);
    for (const file of images) {
      try {
        onAttach(await upload.mutateAsync({ project, bucket, file }));
      } catch (err) {
        // Name the file: with several selected, "that failed" does not say
        // which one is missing from the message about to be sent.
        const why = err instanceof Error ? err.message : String(err);
        setFailed((was) => [...was, `${file.name}: ${why}`]);
      }
    }
  };

  return (
    <div
      className={[styles.field, className].filter(Boolean).join(' ')}
      onPaste={(e) => {
        // A screenshot on the clipboard is a file item. Text pasted alongside
        // it must still reach the textarea, so only an image stops the event.
        if (disabled) return;
        const files = Array.from(e.clipboardData.files);
        if (!files.some((f) => f.type.startsWith('image/'))) return;
        e.preventDefault();
        void take(files);
      }}
    >
      {children}

      {attached.length > 0 && (
        <ul className={styles.strip}>
          {attached.map((image) => (
            <li key={image.url} className={styles.thumb}>
              <img src={image.url} alt={image.name} />
              <span className={styles.name} title={image.name}>
                {image.name}
              </span>
              <button
                type="button"
                aria-label={`Remove ${image.name}`}
                onClick={() => onRemove(image)}
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
          accept="image/*"
          multiple
          hidden
          aria-label="Attach image"
          onChange={(e) => {
            void take(Array.from(e.target.files ?? []));
            // Clear it, or picking the same file twice running fires no change.
            e.target.value = '';
          }}
        />
        <button
          type="button"
          disabled={disabled || upload.isPending}
          onClick={() => picker.current?.click()}
        >
          {upload.isPending ? 'Attaching…' : 'Attach image'}
        </button>
        {failed.length > 0 && (
          <span className={styles.error} role="alert">
            {failed.join('; ')}
          </span>
        )}
      </div>
    </div>
  );
}
