import { useRef, useState } from 'react';
import { AppButton } from '../ui/AppButton';
import styles from './MessageInput.module.css';

export function MessageInput({
  onSend,
  disabled,
}: {
  /** Resolves once the agent took the message; a rejection keeps the text for a retry. */
  onSend: (text: string) => void | Promise<unknown>;
  disabled?: boolean;
}) {
  const [text, setText] = useState('');
  const sendButton = useRef<HTMLButtonElement>(null);
  const trimmed = text.trim();
  // The button owns the send: pending, failed, retried. Enter presses it, so
  // both paths share one state.
  const send = async () => {
    if (!trimmed) return;
    await onSend(trimmed);
    setText('');
  };
  return (
    <div className={styles.form}>
      <textarea
        className={styles.input}
        aria-label="Message to agent"
        placeholder={
          disabled ? 'The agent has exited.' : 'Say something to the agent… (Enter to send)'
        }
        value={text}
        disabled={disabled}
        rows={2}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendButton.current?.click();
          }
        }}
      />
      <AppButton ref={sendButton} disabled={disabled || !trimmed} onClick={send}>
        Send
      </AppButton>
    </div>
  );
}
