import { useRef, useState } from 'react';
import { withoutRef, type Attachment } from '../api/attachments';
import { useLayoutMode } from '../layout/useLayoutMode';
import { AppButton } from '../ui/AppButton';
import { AttachField } from '../ui/AttachField';
import styles from './MessageInput.module.css';

export function MessageInput({
  project,
  bucket,
  onSend,
  onRun,
  disabled,
}: {
  project: string;
  /** Groups this agent's images in the task repo. */
  bucket: string;
  /** Resolves once the agent took the message; a rejection keeps the text for a retry. */
  onSend: (text: string, attachments: Attachment[]) => void | Promise<unknown>;
  /** Resolves once the host ran the command of a `!` line. */
  onRun: (command: string) => void | Promise<unknown>;
  disabled?: boolean;
}) {
  const [text, setText] = useState('');
  const [attached, setAttached] = useState<Attachment[]>([]);
  const sendButton = useRef<HTMLButtonElement>(null);
  // A soft keyboard's Enter means a newline, so only the button sends there.
  // On a hardware keyboard Enter sends, which is what a power tool wants.
  const enterSends = useLayoutMode() === 'wide';
  const trimmed = text.trim();
  // An image with no words is a message in its own right, so either one is
  // enough to send. The server's own rule says the same.
  const canSend = Boolean(trimmed) || attached.length > 0;
  // The button owns the send: pending, failed, retried. Enter presses it, so
  // both paths share one state.
  const send = async () => {
    if (!canSend) return;
    // The `!` prefix is the instruction, so it does not travel with the command.
    // An attached image writes a markdown ref, which also starts with `!`, so
    // only a line with nothing attached can be a command.
    if (attached.length === 0 && trimmed.startsWith('!')) {
      const command = trimmed.slice(1).trim();
      if (!command) return;
      await onRun(command);
      setText('');
      return;
    }
    await onSend(trimmed, attached);
    setText('');
    setAttached([]);
  };
  return (
    <div className={styles.form}>
      <AttachField
        className={styles.attach}
        project={project}
        bucket={bucket}
        attached={attached}
        disabled={disabled}
        onAttach={(a) => {
          setAttached((was) => [...was, a]);
          // The ref goes in the text so the reader sees the image in the
          // transcript; the bytes travel separately, so the model sees it now.
          setText((was) => (was ? `${was}\n\n${a.markdown}` : a.markdown));
        }}
        onRemove={(image) => {
          setAttached((was) => was.filter((w) => w.url !== image.url));
          setText((was) => withoutRef(was, image));
        }}
      >
        <textarea
          className={styles.input}
          aria-label="Message to agent"
          placeholder={
            disabled
              ? 'The agent has exited.'
              : enterSends
                ? 'Say something to the agent, or !run a command… (Enter to send)'
                : 'Say something to the agent, or !run a command…'
          }
          value={text}
          disabled={disabled}
          rows={2}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (enterSends && e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              sendButton.current?.click();
            }
          }}
        />
      </AttachField>
      <AppButton ref={sendButton} disabled={disabled || !canSend} onClick={send}>
        Send
      </AppButton>
    </div>
  );
}
