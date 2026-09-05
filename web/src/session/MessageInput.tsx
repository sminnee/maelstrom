import { useRef, useState } from 'react';
import { useLayoutMode } from '../layout/useLayoutMode';
import { AppButton } from '../ui/AppButton';
import styles from './MessageInput.module.css';

export function MessageInput({
  onSend,
  onRun,
  disabled,
}: {
  /** Resolves once the agent took the message; a rejection keeps the text for a retry. */
  onSend: (text: string) => void | Promise<unknown>;
  /** Resolves once the host ran the command of a `!` line. */
  onRun: (command: string) => void | Promise<unknown>;
  disabled?: boolean;
}) {
  const [text, setText] = useState('');
  const sendButton = useRef<HTMLButtonElement>(null);
  // A soft keyboard's Enter means a newline, so only the button sends there.
  // On a hardware keyboard Enter sends, which is what a power tool wants.
  const enterSends = useLayoutMode() === 'wide';
  const trimmed = text.trim();
  // The button owns the send: pending, failed, retried. Enter presses it, so
  // both paths share one state.
  const send = async () => {
    if (!trimmed) return;
    // The `!` prefix is the instruction, so it does not travel with the command.
    if (trimmed.startsWith('!')) {
      const command = trimmed.slice(1).trim();
      if (!command) return;
      await onRun(command);
      setText('');
      return;
    }
    await onSend(trimmed);
    setText('');
  };
  return (
    <div className={styles.form}>
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
      <AppButton ref={sendButton} disabled={disabled || !trimmed} onClick={send}>
        Send
      </AppButton>
    </div>
  );
}
