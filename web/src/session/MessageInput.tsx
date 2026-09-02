import { useState } from 'react';
import styles from './MessageInput.module.css';

export function MessageInput({
  onSend,
  disabled,
}: {
  /** Resolves true when the agent took the message; false keeps the text for a retry. */
  onSend: (text: string) => Promise<boolean>;
  disabled?: boolean;
}) {
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const send = async () => {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    setSending(true);
    try {
      if (await onSend(trimmed)) setText('');
    } finally {
      setSending(false);
    }
  };
  const blocked = disabled || sending;
  return (
    <form
      className={styles.form}
      onSubmit={(e) => {
        e.preventDefault();
        void send();
      }}
    >
      <textarea
        className={styles.input}
        aria-label="Message to agent"
        placeholder={
          disabled ? 'The agent has exited.' : 'Say something to the agent… (Enter to send)'
        }
        value={text}
        disabled={blocked}
        rows={2}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            void send();
          }
        }}
      />
      <button type="submit" disabled={blocked || !text.trim()}>
        Send
      </button>
    </form>
  );
}
