import { useId, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useUploadAttachment, type Attachment } from '../api/attachments';
import { AppButton } from './AppButton';
import styles from './AttachField.module.css';
import { Dialog, DialogFooter, DialogHeader } from './Dialog';
import dialogStyles from './Dialog.module.css';

/**
 * The attach control every text surface shares: paste, pick, a text note, and
 * a strip of what is attached.
 *
 * It wraps the surface's own textarea rather than owning one, so each caller
 * keeps its own value, label and submit. What it owns is the interaction —
 * reading images off a paste, uploading files, and handing back the markdown
 * to append: an image ref and a thumbnail, or a link and a chip with its size.
 *
 * Paste takes images only; a long block of text goes in a text note.
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
  const [noting, setNoting] = useState(false);

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
        <AppButton disabled={disabled || upload.isPending} onClick={() => setNoting(true)}>
          Attach text note
        </AppButton>
        {failed.length > 0 && (
          <span className={styles.error} role="alert">
            {failed.join('; ')}
          </span>
        )}
        {actions && <span className={styles.end}>{actions}</span>}
      </div>
      {noting && (
        <TextNoteDialog
          onClose={() => setNoting(false)}
          onAttach={async (file) => {
            const why = await takeOne(file);
            if (!why) setNoting(false);
            return why;
          }}
          pending={upload.isPending}
        />
      )}
    </div>
  );
}

/** The name a text note gets when the user gives none. */
const DEFAULT_NOTE_NAME = 'note.txt';

/** An extension the server keeps: short and alphanumeric, as `_SAFE_EXTENSION`. */
const KEPT_EXTENSION = /\.[A-Za-z0-9]{1,10}$/;

/**
 * A dialog that turns a block of text into a file and attaches it.
 *
 * The browser makes the file, so the server takes it as any other upload.
 * It is portalled to the body: the field may sit in a `<form>` or another
 * dialog, and Enter here must not submit that form.
 */
function TextNoteDialog({
  onClose,
  onAttach,
  pending,
}: {
  onClose: () => void;
  /** Resolves to why the upload failed, or `null` once it is attached. */
  onAttach: (file: File) => Promise<string | null>;
  pending: boolean;
}) {
  const [content, setContent] = useState('');
  const [name, setName] = useState(DEFAULT_NOTE_NAME);
  const [error, setError] = useState<string | null>(null);
  const contentRef = useRef<HTMLTextAreaElement>(null);
  const canAttach = content.trim() !== '' && name.trim() !== '' && !pending;
  const attach = async () => {
    if (!canAttach) return;
    const trimmed = name.trim();
    const filename = KEPT_EXTENSION.test(trimmed) ? trimmed : `${trimmed}.txt`;
    setError(await onAttach(new File([content], filename, { type: 'text/plain' })));
  };
  const stop = (e: React.SyntheticEvent) => e.stopPropagation();
  // React types `onCancel` on `<dialog>` only, but the event bubbles to a div.
  const stopAll = {
    onCancel: stop,
    onPointerDown: stop,
    onClick: stop,
    onPaste: stop,
    onFocus: stop,
  } as React.HTMLAttributes<HTMLDivElement>;
  return createPortal(
    // React events bubble through a portal to the field and to any dialog it
    // sits in. Escape here must not close the outer dialog too, a paste here
    // is the note's text and not an image for the field, and a focus here is
    // not a field of the outer dialog to scroll to.
    <div {...stopAll}>
      <Dialog label="Attach text note" onClose={onClose} initialFocus={contentRef}>
        <DialogHeader title="Attach text note" onClose={onClose} />
        <div className={styles.note}>
          <label className={dialogStyles.field}>
            Content
            <textarea
              ref={contentRef}
              rows={12}
              value={content}
              onChange={(e) => setContent(e.target.value)}
            />
          </label>
          <label className={dialogStyles.field}>
            File name
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  void attach();
                }
              }}
            />
          </label>
          {error && (
            <span className={styles.error} role="alert">
              {error}
            </span>
          )}
        </div>
        <DialogFooter>
          <AppButton onClick={onClose}>Cancel</AppButton>
          <AppButton variant="primary" disabled={!canAttach} onClick={() => void attach()}>
            {pending ? 'Attaching…' : 'Attach'}
          </AppButton>
        </DialogFooter>
      </Dialog>
    </div>,
    document.body,
  );
}

/** A byte count in B, KB or MB, as the server's link label gives it. */
function humanSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}
